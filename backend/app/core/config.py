"""Settings read from the project's .env file (one level above backend/)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    database_url: str

    # Where the browser app runs, for CORS.
    web_origin: str = "http://localhost:3000"

    storage_bucket: str = "lab-material"
    max_upload_mb: int = 20

    # Embeddings: "local" runs a small model on this machine; "gemini" calls Google's API.
    embedding_provider: Literal["local", "gemini"] = "local"
    gemini_api_key: str = ""
    gemini_embedding_model: str = "gemini-embedding-001"

    # Language model for writing questions (and later scoring).
    llm_provider: Literal["groq", "gemini"] = "groq"
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    gemini_model: str = "gemini-2.5-flash"
    # Per-minute limits of the provider's tier. The worker paces its calls to stay under them.
    # Defaults are Groq's free tier for gpt-oss-120b; Gemini's free tier allows 10 requests.
    llm_requests_per_minute: int = 30
    llm_tokens_per_minute: int = 8000

    # Text to speech for question audio.
    sarvam_api_key: str = ""
    sarvam_tts_model: str = "bulbul:v3"
    sarvam_speaker: str = ""  # empty = Sarvam's default voice for the model
    # Speech to text for spoken answers. Sarvam's live endpoint takes up to 30 seconds a request.
    sarvam_stt_model: str = "saaras:v4"
    audio_bucket: str = "question-audio"

    chroma_path: str = "./chroma"

    @property
    def chroma_dir(self) -> Path:
        path = Path(self.chroma_path)
        return path if path.is_absolute() else PROJECT_ROOT / path


@lru_cache
def get_settings() -> Settings:
    return Settings()
