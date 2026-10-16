"""Exams: a teacher creates one for a subject and section, opens it, and closes it."""

import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, field_validator

from app.core.auth import CurrentUser, require_teacher
from app.core.db import transaction
from app.modules.questions.generation import pool_slots
from app.modules.sessions.draw import missing_slots

router = APIRouter(tags=["exams"])

Teacher = Annotated[CurrentUser, Depends(require_teacher)]

# No 0/O or 1/I/L: the code is read off a board and typed by a whole batch.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6


def new_join_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


class NewExam(BaseModel):
    subject_id: UUID
    title: str = Field(min_length=1, max_length=200)
    section: str = Field(min_length=1, max_length=50)
    experiment_ids: list[UUID] = Field(min_length=1)
    duration_minutes: int = Field(default=10, ge=1, le=60)
    marks_per_question: float = Field(default=4, gt=0, le=20)
    # Share of a question's marks lost per hint taken.
    hint_penalty: float = Field(default=0.25, ge=0, le=0.5)

    @field_validator("title", "section")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


@router.post("/exams", status_code=status.HTTP_201_CREATED)
def create_exam(new: NewExam, teacher: Teacher) -> dict[str, str]:
    experiment_ids = list(dict.fromkeys(new.experiment_ids))
    with transaction() as conn:
        subject = conn.execute(
            "select question_type_mix from public.subjects where id = %s", (new.subject_id,)
        ).fetchone()
        if subject is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown subject")
        found = conn.execute(
            "select count(*) as n from public.experiments where subject_id = %s and id = any(%s)",
            (new.subject_id, experiment_ids),
        ).fetchone()["n"]
        if found != len(experiment_ids):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Every experiment must belong to the subject")

        config = {
            "experiment_ids": [str(e) for e in experiment_ids],
            # A copy of the subject's mix, not a reference to it: if the subject's rules
            # change later, every student in this exam still gets the same mix.
            "slots": pool_slots(subject["question_type_mix"]),
            "duration_minutes": new.duration_minutes,
            "marks_per_question": new.marks_per_question,
            "hint_penalty": new.hint_penalty,
        }
        for _ in range(5):
            try:
                with conn.transaction():  # a savepoint: a code already taken undoes only this insert
                    exam = conn.execute(
                        """
                        insert into public.exams (teacher_id, subject_id, section, title, join_code, config)
                        values (%s, %s, %s, %s, %s, %s)
                        returning id, join_code
                        """,
                        (teacher.id, new.subject_id, new.section, new.title, new_join_code(), Jsonb(config)),
                    ).fetchone()
                break
            except UniqueViolation:
                continue
        else:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Could not find a free join code, try again")
    return {"id": str(exam["id"]), "join_code": exam["join_code"], "state": "draft"}


def _own_exam(conn: Connection, exam_id: UUID, teacher: CurrentUser) -> dict:
    exam = conn.execute(
        "select id, state, config from public.exams where id = %s and teacher_id = %s for update",
        (exam_id, teacher.id),
    ).fetchone()
    if exam is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam not found")
    return exam


@router.post("/exams/{exam_id}/open")
def open_exam(exam_id: UUID, teacher: Teacher) -> dict[str, str]:
    """Let students join. Refused while any slot has no approved question to draw."""
    with transaction() as conn:
        exam = _own_exam(conn, exam_id, teacher)
        if exam["state"] == "open":
            return {"id": str(exam_id), "state": "open"}
        if exam["state"] != "draft":
            raise HTTPException(status.HTTP_409_CONFLICT, f"The exam is already {exam['state']}")
        missing = missing_slots(conn, teacher.id, exam["config"]["experiment_ids"], exam["config"]["slots"])
        if missing:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "Approve at least one question for: " + "; ".join(missing)
            )
        conn.execute("update public.exams set state = 'open' where id = %s", (exam_id,))
    return {"id": str(exam_id), "state": "open"}


@router.post("/exams/{exam_id}/close")
def close_exam(exam_id: UUID, teacher: Teacher) -> dict[str, str | int]:
    """Stop the exam: no one can join, and every unfinished viva ends with what it has."""
    with transaction() as conn:
        exam = _own_exam(conn, exam_id, teacher)
        if exam["state"] == "draft":
            raise HTTPException(status.HTTP_409_CONFLICT, "The exam has not been opened")
        if exam["state"] != "open":
            return {"id": str(exam_id), "state": exam["state"], "sessions_ended": 0}  # already closed
        conn.execute("update public.exams set state = 'closed' where id = %s", (exam_id,))
        ended = conn.execute(
            """
            update public.sessions set state = 'completed', completed_at = now()
            where exam_id = %s and state <> 'completed'
            """,
            (exam_id,),
        ).rowcount
    return {"id": str(exam_id), "state": "closed", "sessions_ended": ended}
