"""Language models, behind one interface.

The rest of the code asks for JSON that matches a schema and never names a vendor, so
switching between Groq and Gemini is a setting (LLM_PROVIDER), not a code change.
"""

import json
from functools import lru_cache
from typing import Any, Protocol

import httpx

from app.core.config import get_settings


class RateLimited(Exception):
    """The provider said "too many requests". Try again after retry_after seconds."""

    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"rate limited, retry after {retry_after:.0f}s")


class LanguageModel(Protocol):
    model_id: str

    def generate_json(self, system: str, user: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
        """Return the model's answer as JSON that matches schema."""
        ...


class GroqModel:
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self.model_id = model

    def generate_json(self, system: str, user: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
        response = httpx.post(
            self.URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self.model_id,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                # Strict mode: the reply is guaranteed to match the schema's shape.
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "strict": True, "schema": schema},
                },
                "reasoning_effort": "low",
                "max_completion_tokens": 4500,
            },
            timeout=120,
        )
        if response.status_code == 429:
            raise RateLimited(float(response.headers.get("retry-after", 30)))
        response.raise_for_status()
        return json.loads(response.json()["choices"][0]["message"]["content"])


class GeminiModel:
    def __init__(self, api_key: str, model: str) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self.model_id = model

    def generate_json(self, system: str, user: str, schema: dict[str, Any], schema_name: str) -> dict[str, Any]:
        from google.genai import errors, types

        try:
            response = self._client.models.generate_content(
                model=self.model_id,
                contents=user,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_json_schema=schema,
                ),
            )
        except errors.ClientError as error:
            if error.code == 429:
                raise RateLimited(30)
            raise
        return json.loads(response.text)


@lru_cache
def get_language_model() -> LanguageModel:
    settings = get_settings()
    if settings.llm_provider == "gemini":
        if not settings.gemini_api_key:
            raise RuntimeError("LLM_PROVIDER=gemini needs GEMINI_API_KEY in .env")
        return GeminiModel(settings.gemini_api_key, settings.gemini_model)
    if not settings.groq_api_key:
        raise RuntimeError("LLM_PROVIDER=groq needs GROQ_API_KEY in .env")
    return GroqModel(settings.groq_api_key, settings.groq_model)
