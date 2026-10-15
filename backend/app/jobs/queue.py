"""The work queue, kept in the Postgres jobs table.

A job is a row: a type ("ingest", later "score"), a JSON payload, and a status that moves
queued -> running -> done, or back to queued for a retry, or to failed.
"""

from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb


def enqueue(conn: Connection, job_type: str, payload: dict[str, Any]) -> int:
    """Add a job using the caller's connection, so it commits in the caller's transaction.

    That is the point: the document row and its ingest job are saved together or not at all.
    """
    row = conn.execute(
        "insert into public.jobs (type, payload) values (%s, %s) returning id",
        (job_type, Jsonb(payload)),
    ).fetchone()
    return row["id"]
