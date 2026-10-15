"""Lab material chunks in Chroma, found by meaning and filtered by experiment.

Postgres is the source of truth. Everything here can be rebuilt by re-processing the
uploaded documents, so losing the Chroma folder loses no exam data.
Only the worker writes here: Chroma's local mode is not safe for several processes.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

import chromadb

from app.ai.embeddings import get_embedder
from app.core.config import get_settings


@dataclass(frozen=True)
class Chunk:
    experiment_id: str
    index: int
    text: str


@lru_cache
def _client(path: str) -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=path)


def _collection() -> chromadb.Collection:
    embedder = get_embedder()
    name = "lab_material__" + re.sub(r"[^a-zA-Z0-9._-]", "-", embedder.model_id)
    return _client(str(get_settings().chroma_dir)).get_or_create_collection(
        name,
        embedding_function=None,  # we always pass vectors from our own embedder
        configuration={"hnsw": {"space": "cosine"}},
    )


def replace_document_chunks(document_id: str, subject_id: str, chunks: list[Chunk]) -> None:
    """Delete this document's chunks, then add the new ones.

    Deleting first is what makes the ingest job safe to run twice: a retry or a job picked
    up after a crash replaces the chunks instead of storing them a second time.
    """
    collection = _collection()
    collection.delete(where={"document_id": document_id})
    if not chunks:
        return
    collection.add(
        ids=[f"{document_id}:{chunk.index}" for chunk in chunks],
        documents=[chunk.text for chunk in chunks],
        embeddings=get_embedder().embed_documents([chunk.text for chunk in chunks]),
        metadatas=[
            {
                "document_id": document_id,
                "subject_id": subject_id,
                "experiment_id": chunk.experiment_id,
                "chunk_index": chunk.index,
            }
            for chunk in chunks
        ],
    )


def experiment_chunks(document_id: str, experiment_id: str) -> list[str]:
    """All of one experiment's text from one document, in reading order."""
    result = _collection().get(
        where={"$and": [{"document_id": document_id}, {"experiment_id": experiment_id}]},
        include=["documents", "metadatas"],
    )
    ordered = sorted(zip(result["metadatas"], result["documents"]), key=lambda pair: pair[0]["chunk_index"])
    return [text for _, text in ordered]


def count_document_chunks(document_id: str) -> int:
    return len(_collection().get(where={"document_id": document_id}, include=[])["ids"])


def search(query: str, experiment_id: str, limit: int = 5) -> list[dict]:
    """The chunks of one experiment closest in meaning to the query."""
    result = _collection().query(
        query_embeddings=[get_embedder().embed_query(query)],
        n_results=limit,
        where={"experiment_id": experiment_id},
    )
    return [
        {"text": text, "distance": distance, **metadata}
        for text, distance, metadata in zip(
            result["documents"][0], result["distances"][0], result["metadatas"][0]
        )
    ]
