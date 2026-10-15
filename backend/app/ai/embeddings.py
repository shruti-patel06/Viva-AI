"""Turning text into vectors, behind one interface.

The rest of the code calls get_embedder() and never names a vendor, so switching between
the local model and Gemini is a setting (EMBEDDING_PROVIDER), not a code change.

Vectors from different models cannot be compared, so each model gets its own Chroma
collection (named by model_id). Switching provider means re-processing the documents.
"""

from functools import lru_cache
from typing import Protocol

from app.core.config import get_settings


class Embedder(Protocol):
    model_id: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class LocalEmbedder:
    """all-MiniLM-L6-v2, run on this machine through Chroma's built-in ONNX model.

    Free, offline and unlimited. The model (about 80 MB) downloads on first use.
    """

    model_id = "local-all-MiniLM-L6-v2"

    def __init__(self) -> None:
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

        self._embed = DefaultEmbeddingFunction()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(x) for x in vector] for vector in self._embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


class GeminiEmbedder:
    """Google's embedding model. Needs GEMINI_API_KEY; counts against the free-tier limits."""

    BATCH_SIZE = 100
    DIMENSIONS = 768

    def __init__(self, api_key: str, model: str) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model
        self.model_id = model

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        from google.genai import types

        config = types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.DIMENSIONS)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH_SIZE):
            result = self._client.models.embed_content(
                model=self._model, contents=texts[start : start + self.BATCH_SIZE], config=config
            )
            vectors.extend(embedding.values for embedding in result.embeddings)
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_QUERY")[0]


@lru_cache
def get_embedder() -> Embedder:
    settings = get_settings()
    if settings.embedding_provider == "gemini":
        if not settings.gemini_api_key:
            raise RuntimeError("EMBEDDING_PROVIDER=gemini needs GEMINI_API_KEY in .env")
        return GeminiEmbedder(settings.gemini_api_key, settings.gemini_embedding_model)
    return LocalEmbedder()
