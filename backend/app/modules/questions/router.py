"""The question pool: generating it, and the teacher's approve / edit / reject decisions."""

from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg import Connection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from app.core import storage
from app.core.auth import CurrentUser, require_teacher
from app.core.config import get_settings
from app.core.db import transaction
from app.jobs.queue import enqueue
from app.modules.questions.audio_job import JOB_TYPE as AUDIO_JOB
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


def _own_question(conn: Connection, question_id: UUID, teacher: CurrentUser) -> dict:
    question = conn.execute(
        "select id, status, audio_path from public.questions where id = %s and teacher_id = %s for update",
        (question_id, teacher.id),
    ).fetchone()
    if question is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    return question


class QuestionEdit(BaseModel):
    """Fields a teacher may change. Anything left out stays as it is."""

    question_text: str | None = Field(default=None, min_length=1, max_length=1000)
    reference_answer: str | None = Field(default=None, min_length=1, max_length=2000)
    key_points: list[str] | None = Field(default=None, min_length=1, max_length=6)
    hints: list[str] | None = Field(default=None, max_length=3)
    difficulty: Literal["easy", "medium", "hard"] | None = None

    @field_validator("question_text", "reference_answer")
    @classmethod
    def not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value.strip() if value else value

    @field_validator("key_points", "hints")
    @classmethod
    def drop_blank_items(cls, value: list[str] | None) -> list[str] | None:
        return [item.strip() for item in value if item.strip()] if value is not None else None


@router.patch("/questions/{question_id}")
def edit_question(question_id: UUID, edit: QuestionEdit, teacher: Teacher) -> dict[str, str]:
    """Save the teacher's changes. An edited question goes back to draft and loses its audio,
    so nothing reaches a student until the teacher approves the new wording."""
    changes = edit.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Nothing to change")
    if "key_points" in changes and not changes["key_points"]:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A question needs at least one key point")

    with transaction() as conn:
        _own_question(conn, question_id, teacher)
        # Column names come from QuestionEdit's fixed fields, never from the request,
        # so building this part of the SQL is safe; the values are still parameters.
        assignments = ", ".join(f"{field} = %s" for field in changes)
        values = [Jsonb(v) if isinstance(v, list) else v for v in changes.values()]
        conn.execute(
            f"""
            update public.questions
            set {assignments}, status = 'draft', audio_path = null, updated_at = now()
            where id = %s
            """,
            (*values, question_id),
        )
    return {"id": str(question_id), "status": "draft"}


@router.post("/questions/{question_id}/approve")
def approve_question(question_id: UUID, teacher: Teacher) -> dict[str, str]:
    """Approve, and queue its audio. Approving again retries audio that failed."""
    with transaction() as conn:
        question = _own_question(conn, question_id, teacher)
        conn.execute(
            "update public.questions set status = 'approved', updated_at = now() where id = %s",
            (question_id,),
        )
        if question["audio_path"] is None:
            enqueue(conn, AUDIO_JOB, {"question_id": str(question_id), "teacher_id": str(teacher.id)})
    return {"id": str(question_id), "status": "approved"}


@router.post("/questions/{question_id}/reject")
def reject_question(question_id: UUID, teacher: Teacher) -> dict[str, str]:
    with transaction() as conn:
        _own_question(conn, question_id, teacher)
        conn.execute(
            "update public.questions set status = 'rejected', updated_at = now() where id = %s",
            (question_id,),
        )
    return {"id": str(question_id), "status": "rejected"}


@router.get("/questions/{question_id}/audio")
def question_audio(question_id: UUID, teacher: Teacher) -> dict[str, str]:
    """A short-lived link to the question's audio. The bucket itself stays private."""
    with transaction() as conn:
        question = _own_question(conn, question_id, teacher)
    if question["audio_path"] is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No audio yet")
    return {"url": storage.signed_url(get_settings().audio_bucket, question["audio_path"])}
