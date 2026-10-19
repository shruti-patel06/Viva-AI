"""The work queue, kept in the Postgres jobs table.

A job is a row: a type ("ingest", later "score"), a JSON payload, and a status that moves
queued -> running -> done, or back to queued for a retry, or to failed.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

MAX_ATTEMPTS = 3
FIRST_RETRY_DELAY = timedelta(seconds=30)
# Longer than any job should take. A job running longer than this was abandoned.
STALE_AFTER = timedelta(minutes=10)


class PermanentJobError(Exception):
    """Retrying will not help, such as a manual missing an experiment title. Fail at once."""


class RetryLater(Exception):
    """Nothing went wrong, but the job cannot run yet (an AI service is rate limiting us).

    The job goes back in the queue for `delay` without using up one of its attempts.
    """

    def __init__(self, delay: timedelta, reason: str):
        self.delay = delay
        super().__init__(reason)


@dataclass(frozen=True)
class Job:
    id: int
    type: str
    payload: dict[str, Any]
    attempts: int


def enqueue(conn: Connection, job_type: str, payload: dict[str, Any], priority: int = 0) -> int:
    """Add a job using the caller's connection, so it commits in the caller's transaction.

    That is the point: the document row and its ingest job are saved together or not at all.
    Higher priority runs first: work a student is waiting for goes ahead of scoring.
    """
    row = conn.execute(
        "insert into public.jobs (type, payload, priority) values (%s, %s, %s) returning id",
        (job_type, Jsonb(payload), priority),
    ).fetchone()
    return row["id"]


def claim_next(conn: Connection, job_types: Sequence[str]) -> Job | None:
    """Take the most urgent job that is due (highest priority, then oldest), or None.

    FOR UPDATE locks the chosen row; SKIP LOCKED makes another worker running the same
    query skip that row instead of waiting for it. So two workers never get the same job,
    and neither blocks the other.
    """
    row = conn.execute(
        """
        update public.jobs
        set status = 'running', attempts = attempts + 1, locked_at = now(), updated_at = now()
        where id = (
            select id from public.jobs
            where status = 'queued' and run_after <= now() and type = any(%s)
            order by priority desc, run_after, id
            for update skip locked
            limit 1
        )
        returning id, type, payload, attempts
        """,
        (list(job_types),),
    ).fetchone()
    return Job(**row) if row else None


def complete(conn: Connection, job_id: int) -> None:
    conn.execute(
        "update public.jobs set status = 'done', locked_at = null, updated_at = now() where id = %s",
        (job_id,),
    )


def retry_later(conn: Connection, job: Job, error: str) -> None:
    """Back to the queue, waiting longer after each failure: 30s, then 60s, then 120s..."""
    delay = FIRST_RETRY_DELAY * 2 ** (job.attempts - 1)
    conn.execute(
        """
        update public.jobs
        set status = 'queued', run_after = now() + %s, last_error = %s,
            locked_at = null, updated_at = now()
        where id = %s
        """,
        (delay, error, job.id),
    )


def postpone(conn: Connection, job: Job, delay: timedelta, reason: str) -> None:
    """Back to the queue after `delay`, giving back the attempt this run used."""
    conn.execute(
        """
        update public.jobs
        set status = 'queued', run_after = now() + %s, attempts = attempts - 1,
            last_error = %s, locked_at = null, updated_at = now()
        where id = %s
        """,
        (delay, reason, job.id),
    )


def fail(conn: Connection, job_id: int, error: str) -> None:
    conn.execute(
        """
        update public.jobs
        set status = 'failed', last_error = %s, locked_at = null, updated_at = now()
        where id = %s
        """,
        (error, job_id),
    )


def requeue_stale(conn: Connection) -> int:
    """Put abandoned jobs back in the queue. Returns how many were found."""
    return conn.execute(
        """
        update public.jobs
        set status = 'queued', locked_at = null, updated_at = now(),
            last_error = 'abandoned: the worker stopped while running this job'
        where status = 'running' and locked_at < now() - %s
        """,
        (STALE_AFTER,),
    ).rowcount
