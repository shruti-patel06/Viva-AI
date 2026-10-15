"""The ingest job end to end: storage -> text -> experiments -> Chroma. Needs `supabase start`.

Uses the local embedding model (downloads about 80 MB on the first run) and a temporary
Chroma folder, so it never touches the real one.
"""

from uuid import uuid4

import pytest

from app.core import storage
from app.core.config import get_settings
from app.core.db import transaction
from app.jobs import queue
from app.modules.ingestion import ingest_job, vector_store
from app.worker.runner import run_one
from tests.sample_manual import EXPERIMENTS, build_sample_manual

TEACHER_ID = "44444444-0000-0000-0000-000000000001"


@pytest.fixture
def temp_chroma(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "chroma_path", str(tmp_path / "chroma"))
    monkeypatch.setattr(get_settings(), "embedding_provider", "local")


@pytest.fixture
def subject():
    """A test subject with the sample manual's experiments, removed afterwards."""
    with transaction() as conn:
        conn.execute(
            """
            insert into auth.users (id, email, is_anonymous, raw_app_meta_data)
            values (%s, 'ingest.teacher@test.local', false, '{"role": "teacher"}')
            on conflict (id) do nothing
            """,
            (TEACHER_ID,),
        )
        subject_id = conn.execute(
            "insert into public.subjects (name) values (%s) returning id", (f"Test subject {uuid4()}",)
        ).fetchone()["id"]
        experiment_ids = {}
        for position, title in enumerate(EXPERIMENTS, start=1):
            experiment_ids[title] = str(
                conn.execute(
                    "insert into public.experiments (subject_id, position, title) values (%s, %s, %s) returning id",
                    (subject_id, position, title),
                ).fetchone()["id"]
            )
    yield subject_id, experiment_ids
    with transaction() as conn:
        conn.execute("delete from public.jobs where payload->>'document_id' in "
                     "(select id::text from public.documents where subject_id = %s)", (subject_id,))
        conn.execute("delete from public.documents where subject_id = %s", (subject_id,))
        conn.execute("delete from public.subjects where id = %s", (subject_id,))
        conn.execute("delete from auth.users where id = %s", (TEACHER_ID,))


def upload_manual(subject_id, pdf: bytes) -> str:
    document_id = str(uuid4())
    path = f"{TEACHER_ID}/{document_id}.pdf"
    storage.upload(path, pdf, "application/pdf")
    with transaction() as conn:
        conn.execute(
            """
            insert into public.documents (id, teacher_id, subject_id, file_name, storage_path)
            values (%s, %s, %s, 'manual.pdf', %s)
            """,
            (document_id, TEACHER_ID, subject_id, path),
        )
    return document_id


def document(document_id: str) -> dict:
    with transaction() as conn:
        return conn.execute("select status, error, storage_path from public.documents where id = %s",
                            (document_id,)).fetchone()


def test_manual_becomes_searchable_by_experiment(temp_chroma, subject):
    subject_id, experiment_ids = subject
    document_id = upload_manual(subject_id, build_sample_manual())

    ingest_job.run({"document_id": document_id})

    assert document(document_id)["status"] == "ready"
    stack_id = experiment_ids["Stack Using Arrays"]
    hits = vector_store.search("what happens when you pop an empty stack", stack_id, limit=3)
    assert hits and all(hit["experiment_id"] == stack_id for hit in hits)
    assert "underflow" in hits[0]["text"]
    storage.remove(document(document_id)["storage_path"])


def test_running_the_job_twice_keeps_one_copy_of_the_chunks(temp_chroma, subject):
    subject_id, _ = subject
    document_id = upload_manual(subject_id, build_sample_manual())

    ingest_job.run({"document_id": document_id})
    first = vector_store.count_document_chunks(document_id)
    ingest_job.run({"document_id": document_id})  # a retry, or a pick-up after a crash

    assert first > 0
    assert vector_store.count_document_chunks(document_id) == first
    storage.remove(document(document_id)["storage_path"])


def test_manual_missing_titles_fails_at_once_and_says_which(temp_chroma, subject):
    subject_id, _ = subject
    only_three = dict(list(EXPERIMENTS.items())[:3])
    document_id = upload_manual(subject_id, build_sample_manual(only_three))
    with transaction() as conn:
        job_id = queue.enqueue(conn, "ingest", {"document_id": document_id})

    run_one({"ingest": ingest_job.handler})

    failed = document(document_id)
    assert failed["status"] == "failed"
    assert "Stack Using Arrays" in failed["error"] and "Graph Traversal" in failed["error"]
    with transaction() as conn:
        job = conn.execute("select status, attempts from public.jobs where id = %s", (job_id,)).fetchone()
    assert (job["status"], job["attempts"]) == ("failed", 1)  # no pointless retries
    storage.remove(failed["storage_path"])
