"""The ingest job end to end: storage -> text -> experiments -> Chroma. Needs `supabase start`."""

from app.core.db import transaction
from app.jobs import queue
from app.modules.ingestion import ingest_job, vector_store
from app.worker.runner import run_one
from tests.sample_manual import EXPERIMENTS, build_sample_manual


def document(document_id: str) -> dict:
    with transaction() as conn:
        return conn.execute("select status, error from public.documents where id = %s", (document_id,)).fetchone()


def test_manual_becomes_searchable_by_experiment(temp_chroma, subject, upload_manual):
    document_id = upload_manual(build_sample_manual())

    ingest_job.run({"document_id": document_id})

    assert document(document_id)["status"] == "ready"
    stack_id = subject["experiments"]["Stack Using Arrays"]
    hits = vector_store.search("what happens when you pop an empty stack", stack_id, limit=3)
    assert hits and all(hit["experiment_id"] == stack_id for hit in hits)
    assert "underflow" in hits[0]["text"]


def test_running_the_job_twice_keeps_one_copy_of_the_chunks(temp_chroma, upload_manual):
    document_id = upload_manual(build_sample_manual())

    ingest_job.run({"document_id": document_id})
    first = vector_store.count_document_chunks(document_id)
    ingest_job.run({"document_id": document_id})  # a retry, or a pick-up after a crash

    assert first > 0
    assert vector_store.count_document_chunks(document_id) == first


def test_manual_missing_titles_fails_at_once_and_says_which(temp_chroma, upload_manual):
    only_three = dict(list(EXPERIMENTS.items())[:3])
    document_id = upload_manual(build_sample_manual(only_three))
    with transaction() as conn:
        job_id = queue.enqueue(conn, "ingest", {"document_id": document_id})

    run_one({"ingest": ingest_job.handler})

    failed = document(document_id)
    assert failed["status"] == "failed"
    assert "Stack Using Arrays" in failed["error"] and "Graph Traversal" in failed["error"]
    with transaction() as conn:
        job = conn.execute("select status, attempts from public.jobs where id = %s", (job_id,)).fetchone()
    assert (job["status"], job["attempts"]) == ("failed", 1)  # no pointless retries
