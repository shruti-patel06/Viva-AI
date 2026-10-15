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

    chroma_path: str = "./chroma"

    @property
    def chroma_dir(self) -> Path:
        path = Path(self.chroma_path)
        return path if path.is_absolute() else PROJECT_ROOT / path


@lru_cache
def get_settings() -> Settings:
    return Settings()
