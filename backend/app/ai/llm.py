"""Language models, behind one interface.

The rest of the code asks for JSON that matches a schema and never names a vendor, so
switching between Groq and Gemini is a setting (LLM_PROVIDER), not a code change.

Every call first waits for room under the provider's per-minute limits (see rate_limit.py),
so the same key can be shared by question writing and scoring without being refused.
"""

import json
import time
from functools import lru_cache
from typing import Any, Protocol

import httpx

from app.ai import calls
from app.ai.rate_limit import RateLimiter
from app.core.config import get_settings


class RateLimited(Exception):
    """The provider said "too many requests". Try again after retry_after seconds."""

    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"rate limited, retry after {retry_after:.0f}s")


class LanguageModel(Protocol):
    model_id: str

    def generate_json(
        self, system: str, user: str, schema: dict[str, Any], schema_name: str, max_tokens: int = 4500
    ) -> dict[str, Any]:
        """Return the model's answer as JSON that matches schema. max_tokens caps the reply."""
        ...


def estimate_tokens(system: str, user: str, schema: dict[str, Any], max_tokens: int) -> int:
    """A cautious guess at what a call will use, made before it is sent: about 4 characters
    per prompt token (the schema is sent too), and half the reply cap. The real count
    replaces it once the provider replies."""
    prompt_chars = len(system) + len(user) + len(json.dumps(schema))
    return prompt_chars // 4 + max_tokens // 2


class GroqModel:
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: str, model: str, limiter: RateLimiter) -> None:
        self._api_key = api_key
        self._limiter = limiter
        self.model_id = model

    def generate_json(
        self, system: str, user: str, schema: dict[str, Any], schema_name: str, max_tokens: int = 4500
    ) -> dict[str, Any]:
        estimate = estimate_tokens(system, user, schema, max_tokens)
        waited = self._limiter.acquire(estimate)
        started = time.monotonic()
        try:
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
                    "max_completion_tokens": max_tokens,
                },
                timeout=120,
            )
        except httpx.HTTPError as error:
            calls.record("groq", self.model_id, schema_name, "error", time.monotonic() - started, waited,
                         error=f"{type(error).__name__}: {error}")
            raise
        took = time.monotonic() - started
        remaining = response.headers.get("x-ratelimit-remaining-tokens")
        remaining = int(remaining) if remaining and remaining.isdigit() else None
        if response.status_code == 429:
            # Nothing was used, but the provider's minute is spent: make the next call wait.
            self._limiter.settle(estimate, 0, remaining=remaining or 0)
            calls.record("groq", self.model_id, schema_name, "rate_limited", took, waited)
            raise RateLimited(float(response.headers.get("retry-after", 30)))
        if response.is_error:
            calls.record("groq", self.model_id, schema_name, "error", took, waited,
                         error=f"HTTP {response.status_code}: {response.text[:200]}")
            response.raise_for_status()
        used = response.json().get("usage", {}).get("total_tokens", estimate)
        self._limiter.settle(estimate, used, remaining)
        calls.record("groq", self.model_id, schema_name, "ok", took, waited, tokens=used)
        return json.loads(response.json()["choices"][0]["message"]["content"])


class GeminiModel:
    def __init__(self, api_key: str, model: str, limiter: RateLimiter) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._limiter = limiter
        self.model_id = model

    def generate_json(
        self, system: str, user: str, schema: dict[str, Any], schema_name: str, max_tokens: int = 4500
    ) -> dict[str, Any]:
        from google.genai import errors, types

        estimate = estimate_tokens(system, user, schema, max_tokens)
        waited = self._limiter.acquire(estimate)
        started = time.monotonic()
        try:
            # No reply cap here: Gemini counts its thinking against the cap, and a cut-off
            # reply is not valid JSON. max_tokens only shapes the estimate.
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
            took = time.monotonic() - started
            if error.code == 429:
                self._limiter.settle(estimate, 0, remaining=0)
                calls.record("gemini", self.model_id, schema_name, "rate_limited", took, waited)
                raise RateLimited(30)
            calls.record("gemini", self.model_id, schema_name, "error", took, waited, error=str(error))
            raise
        except Exception as error:
            calls.record("gemini", self.model_id, schema_name, "error", time.monotonic() - started, waited,
                         error=f"{type(error).__name__}: {error}")
            raise
        used = getattr(response.usage_metadata, "total_token_count", None) or estimate
        self._limiter.settle(estimate, used)
        calls.record("gemini", self.model_id, schema_name, "ok", time.monotonic() - started, waited, tokens=used)
        return json.loads(response.text)


@lru_cache
def get_language_model() -> LanguageModel:
    settings = get_settings()
    # One limiter per process, shared by every kind of call, because the limits belong to the key.
    limiter = RateLimiter(settings.llm_requests_per_minute, settings.llm_tokens_per_minute)
    if settings.llm_provider == "gemini":
        if not settings.gemini_api_key:
            raise RuntimeError("LLM_PROVIDER=gemini needs GEMINI_API_KEY in .env")
        return GeminiModel(settings.gemini_api_key, settings.gemini_model, limiter)
    if not settings.groq_api_key:
        raise RuntimeError("LLM_PROVIDER=groq needs GROQ_API_KEY in .env")
    return GroqModel(settings.groq_api_key, settings.groq_model, limiter)
