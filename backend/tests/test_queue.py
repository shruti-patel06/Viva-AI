"""Queue behaviour against the local database. Needs `supabase start`.

Tests use their own job types (test_*), so they never touch real ingest jobs.
"""

from datetime import timedelta

import pytest

from app.core.db import get_pool, transaction
from app.jobs import queue
from app.jobs.queue import MAX_ATTEMPTS, PermanentJobError
from app.worker.runner import Handler, run_one


@pytest.fixture(autouse=True)
def clean_test_jobs():
    def clean():
        with transaction() as conn:
            conn.execute("delete from public.jobs where type like 'test_%%'")

    clean()
    yield
    clean()


def add_job(job_type: str = "test_job") -> int:
    with transaction() as conn:
        return queue.enqueue(conn, job_type, {"n": 1})


def job_row(job_id: int) -> dict:
    with transaction() as conn:
        return conn.execute("select * from public.jobs where id = %s", (job_id,)).fetchone()


def make_due(job_id: int) -> None:
    """Skip the retry wait, as if the time had passed."""
    with transaction() as conn:
        conn.execute("update public.jobs set run_after = now() where id = %s", (job_id,))


class Recorder:
    """A fake handler that records calls and can be told to fail."""

    def __init__(self, error: Exception | None = None):
        self.error = error
        self.runs = 0
        self.gave_up_with: str | None = None

    def handler(self) -> Handler:
        return Handler(run=self._run, on_give_up=self._give_up)

    def _run(self, payload):
        self.runs += 1
        if self.error:
            raise self.error

    def _give_up(self, payload, reason):
        self.gave_up_with = reason


def test_successful_job_is_done():
    job_id = add_job()
    recorder = Recorder()

    assert run_one({"test_job": recorder.handler()}) is True
    assert recorder.runs == 1
    assert job_row(job_id)["status"] == "done"
    assert run_one({"test_job": recorder.handler()}) is False


def test_two_workers_never_claim_the_same_job():
    only_job = add_job()
    pool = get_pool()
    with pool.connection() as worker_a, pool.connection() as worker_b:
        # Worker A claims inside a transaction it has not committed yet, so the row is locked.
        with worker_a.transaction():
            claimed_by_a = queue.claim_next(worker_a, ["test_job"])
            # Worker B runs the same query at the same moment: it skips the locked row
            # instead of waiting, and finds nothing else to do.
            with worker_b.transaction():
                claimed_by_b = queue.claim_next(worker_b, ["test_job"])

    assert claimed_by_a.id == only_job
    assert claimed_by_b is None


def test_failing_job_retries_with_growing_wait_then_gives_up():
    job_id = add_job()
    recorder = Recorder(error=RuntimeError("AI service timed out"))
    handlers = {"test_job": recorder.handler()}

    run_one(handlers)
    first = job_row(job_id)
    assert first["status"] == "queued"
    assert first["attempts"] == 1
    assert "timed out" in first["last_error"]

    # Not due yet, so the worker leaves it alone.
    assert run_one(handlers) is False

    make_due(job_id)
    with transaction() as conn:
        before = conn.execute("select now() as t").fetchone()["t"]
    run_one(handlers)
    second = job_row(job_id)
    assert second["status"] == "queued"
    assert second["run_after"] - before >= queue.FIRST_RETRY_DELAY * 2 - timedelta(seconds=1)

    for _ in range(MAX_ATTEMPTS - 2):
        make_due(job_id)
        run_one(handlers)

    assert job_row(job_id)["status"] == "failed"
    assert recorder.runs == MAX_ATTEMPTS
    assert "timed out" in recorder.gave_up_with


def test_rate_limited_job_waits_without_using_an_attempt():
    job_id = add_job()
    recorder = Recorder(error=queue.RetryLater(timedelta(seconds=20), "AI service busy"))

    run_one({"test_job": recorder.handler()})

    row = job_row(job_id)
    assert row["status"] == "queued"
    assert row["attempts"] == 0
    assert run_one({"test_job": recorder.handler()}) is False  # waiting out the delay


def test_permanent_error_fails_without_retrying():
    job_id = add_job()
    recorder = Recorder(error=PermanentJobError("experiment titles not found: Graph Traversal"))

    run_one({"test_job": recorder.handler()})

    assert job_row(job_id)["status"] == "failed"
    assert recorder.runs == 1
    assert "Graph Traversal" in recorder.gave_up_with


def test_job_abandoned_by_a_crashed_worker_is_picked_up_again():
    job_id = add_job()
    # A worker claimed the job and then died: it is "running" with an old lock.
    with transaction() as conn:
        queue.claim_next(conn, ["test_job"])
        conn.execute(
            "update public.jobs set locked_at = now() - %s where id = %s",
            (queue.STALE_AFTER + timedelta(minutes=1), job_id),
        )

    recorder = Recorder()
    assert run_one({"test_job": recorder.handler()}) is False  # still looks busy

    with transaction() as conn:
        assert queue.requeue_stale(conn) >= 1
    assert run_one({"test_job": recorder.handler()}) is True

    row = job_row(job_id)
    assert row["status"] == "done"
    assert row["attempts"] == 2
    assert recorder.runs == 1


def test_job_abandoned_on_its_last_attempt_is_given_up():
    job_id = add_job()
    with transaction() as conn:
        conn.execute(
            "update public.jobs set status = 'running', attempts = %s, locked_at = now() - %s where id = %s",
            (MAX_ATTEMPTS, queue.STALE_AFTER + timedelta(minutes=1), job_id),
        )
        queue.requeue_stale(conn)

    recorder = Recorder()
    run_one({"test_job": recorder.handler()})

    assert job_row(job_id)["status"] == "failed"
    assert recorder.runs == 0
    assert recorder.gave_up_with is not None


def test_a_higher_priority_job_is_claimed_first():
    with transaction() as conn:
        older = queue.enqueue(conn, "test_job", {"n": 1})
        urgent = queue.enqueue(conn, "test_job", {"n": 2}, priority=10)

    with transaction() as conn:
        first = queue.claim_next(conn, ["test_job"])
    with transaction() as conn:
        second = queue.claim_next(conn, ["test_job"])

    assert (first.id, second.id) == (urgent, older)
