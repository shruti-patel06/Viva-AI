"""The question pool: generating it, and the teacher's approve / edit / reject decisions."""

from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg import Connection

from app.core.auth import CurrentUser, require_teacher
from app.core.db import transaction
from app.jobs.queue import enqueue
from app.modules.questions.generate_job import JOB_TYPE as GENERATE_JOB

router = APIRouter(tags=["questions"])

Teacher = Annotated[CurrentUser, Depends(require_teacher)]


def _own_document(conn: Connection, document_id: UUID, teacher: CurrentUser) -> dict:
    document = conn.execute(
        "select id, subject_id, status from public.documents where id = %s and teacher_id = %s",
        (document_id, teacher.id),
    ).fetchone()
    if document is None:
        # 404, not 403: another teacher's document should look like it does not exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return document


@router.post("/documents/{document_id}/generate-questions", status_code=status.HTTP_202_ACCEPTED)
def generate_questions(document_id: UUID, teacher: Teacher) -> dict[str, int | str]:
    """Queue one generation job per experiment. Questions arrive as drafts."""
    with transaction() as conn:
        document = _own_document(conn, document_id, teacher)
        if document["status"] != "ready":
            raise HTTPException(status.HTTP_409_CONFLICT, "The document has not finished processing")
        busy = conn.execute(
            """
            select 1 from public.jobs
            where type = %s and payload->>'document_id' = %s and status in ('queued', 'running')
            limit 1
            """,
            (GENERATE_JOB, str(document_id)),
        ).fetchone()
        if busy:
            raise HTTPException(status.HTTP_409_CONFLICT, "Questions are already being generated")

        batch_id = str(uuid4())
        experiments = conn.execute(
            "select id from public.experiments where subject_id = %s order by position",
            (document["subject_id"],),
        ).fetchall()
        for experiment in experiments:
            enqueue(
                conn,
                GENERATE_JOB,
                {"document_id": str(document_id), "experiment_id": str(experiment["id"]), "batch_id": batch_id},
            )
    return {"batch_id": batch_id, "experiments": len(experiments)}


@router.get("/documents/{document_id}/generation")
def generation_status(document_id: UUID, teacher: Teacher) -> dict:
    """Progress of the latest generation run for this document, one row per experiment."""
    with transaction() as conn:
        _own_document(conn, document_id, teacher)
        jobs = conn.execute(
            """
            with latest as (
                select payload->>'batch_id' as batch_id from public.jobs
                where type = %s and payload->>'document_id' = %s
                order by created_at desc limit 1
            )
            select e.title, e.position, j.status, j.attempts, j.last_error
            from public.jobs j
            join latest on j.payload->>'batch_id' = latest.batch_id
            join public.experiments e on e.id = (j.payload->>'experiment_id')::uuid
            order by e.position
            """,
            (GENERATE_JOB, str(document_id)),
        ).fetchall()
    counts = {s: sum(1 for j in jobs if j["status"] == s) for s in ("queued", "running", "done", "failed")}
    return {
        **counts,
        "experiments": [
            {
                "title": j["title"],
                "status": j["status"],
                # A queued job with an error is waiting out a retry or a rate limit.
                "note": j["last_error"] if j["status"] in ("queued", "failed") else None,
            }
            for j in jobs
        ],
    }
