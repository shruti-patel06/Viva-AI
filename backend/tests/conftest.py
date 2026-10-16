from typing import Annotated
from uuid import UUID, uuid4

import pytest
from fastapi import Header
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app.core import storage
from app.core.auth import CurrentUser, require_student, require_teacher
from app.core.config import get_settings
from app.core.db import close_pool, transaction
from app.main import app
from app.modules.exams.router import new_join_code
from app.modules.questions.generation import pool_slots
from tests.sample_manual import EXPERIMENTS

TEACHER_ID = "44444444-0000-0000-0000-000000000001"
OTHER_TEACHER = "44444444-0000-0000-0000-000000000002"
STUDENTS = [f"55555555-0000-0000-0000-00000000000{n}" for n in range(1, 5)]


@pytest.fixture(autouse=True, scope="session")
def database_pool():
    yield
    close_pool()


@pytest.fixture
def temp_chroma(tmp_path, monkeypatch):
    """A throwaway Chroma folder and the local embedding model (about 80 MB on first run)."""
    monkeypatch.setattr(get_settings(), "chroma_path", str(tmp_path / "chroma"))
    monkeypatch.setattr(get_settings(), "embedding_provider", "local")


@pytest.fixture
def subject():
    """A test teacher, and a subject with the sample manual's experiments and the Data
    Structures question mix. Everything it made is removed afterwards."""
    with transaction() as conn:
        conn.execute(
            """
            insert into auth.users (id, email, is_anonymous, raw_app_meta_data)
            values (%s, 'test.teacher@test.local', false, '{"role": "teacher"}')
            on conflict (id) do nothing
            """,
            (TEACHER_ID,),
        )
        subject_id = conn.execute(
            """
            insert into public.subjects (name, question_type_mix)
            select %s, question_type_mix from public.subjects where name = 'Data Structures'
            returning id
            """,
            (f"Test subject {uuid4()}",),
        ).fetchone()["id"]
        experiment_ids = {}
        for position, title in enumerate(EXPERIMENTS, start=1):
            experiment_ids[title] = str(
                conn.execute(
                    "insert into public.experiments (subject_id, position, title) values (%s, %s, %s) returning id",
                    (subject_id, position, title),
                ).fetchone()["id"]
            )
    stored: list[tuple[str, str]] = []
    yield {"id": subject_id, "experiments": experiment_ids, "stored": stored}

    for bucket, path in stored:
        storage.remove(bucket, path)
    with transaction() as conn:
        conn.execute(
            "delete from public.jobs where payload->>'document_id' in "
            "(select id::text from public.documents where subject_id = %s)",
            (subject_id,),
        )
        conn.execute("delete from public.jobs where payload->>'teacher_id' = %s", (TEACHER_ID,))
        # Exams first: their sessions point at the questions and the student logins.
        conn.execute("delete from public.exams where teacher_id = %s", (TEACHER_ID,))
        conn.execute("delete from auth.users where id = any(%s::uuid[])", (STUDENTS,))
        conn.execute("delete from public.questions where teacher_id = %s", (TEACHER_ID,))
        conn.execute("delete from public.documents where subject_id = %s", (subject_id,))
        conn.execute("delete from public.subjects where id = %s", (subject_id,))
        conn.execute("delete from auth.users where id = %s", (TEACHER_ID,))


@pytest.fixture
def upload_manual(subject):
    """Store a PDF and its documents row, as the upload endpoint does."""

    def upload(pdf: bytes) -> str:
        document_id = str(uuid4())
        path = f"{TEACHER_ID}/{document_id}.pdf"
        storage.upload(get_settings().storage_bucket, path, pdf, "application/pdf")
        subject["stored"].append((get_settings().storage_bucket, path))
        with transaction() as conn:
            conn.execute(
                """
                insert into public.documents (id, teacher_id, subject_id, file_name, storage_path)
                values (%s, %s, %s, 'manual.pdf', %s)
                """,
                (document_id, TEACHER_ID, subject["id"], path),
            )
        return document_id

    return upload


@pytest.fixture
def login():
    """login(user_id) gives a client acting as that user. The token check is replaced by a
    header naming the user, so one test can act as a teacher and several students at once."""

    def as_role(role: str):
        def fake(x_test_user: Annotated[str, Header()]) -> CurrentUser:
            return CurrentUser(id=UUID(x_test_user), role=role)

        return fake

    app.dependency_overrides[require_teacher] = as_role("teacher")
    app.dependency_overrides[require_student] = as_role("student")
    yield lambda user_id: TestClient(app, headers={"X-Test-User": user_id})
    app.dependency_overrides.clear()


@pytest.fixture
def students(subject) -> list[str]:
    """Anonymous logins, as students get. The sign-up trigger makes them students."""
    with transaction() as conn:
        for student_id in STUDENTS:
            conn.execute(
                "insert into auth.users (id, is_anonymous) values (%s, true) on conflict (id) do nothing",
                (student_id,),
            )
    return STUDENTS


@pytest.fixture
def pool(subject) -> dict:
    """Stack Using Arrays with three approved questions in every pool slot, plus a draft and
    a rejected question per slot that must never be drawn."""
    experiment_id = subject["experiments"]["Stack Using Arrays"]
    approved: dict[tuple[str, str], list[str]] = {}
    with transaction() as conn:
        mix = conn.execute(
            "select question_type_mix from public.subjects where id = %s", (subject["id"],)
        ).fetchone()["question_type_mix"]
        slots = pool_slots(mix)
        for slot in slots:
            for n, status in enumerate(["approved", "approved", "approved", "draft", "rejected"], start=1):
                question_id = conn.execute(
                    """
                    insert into public.questions (teacher_id, experiment_id, type, difficulty, question_text,
                                                  reference_answer, key_points, hints, status)
                    values (%s, %s, %s, %s, %s, 'Secret reference answer.', %s, %s, %s)
                    returning id
                    """,
                    (
                        TEACHER_ID, experiment_id, slot["type"], slot["difficulty"],
                        f"{slot['type']} question {n}?", Jsonb(["secret key point"]),
                        Jsonb(["gentle hint", "strong hint"]), status,
                    ),
                ).fetchone()["id"]
                if status == "approved":
                    approved.setdefault((slot["type"], slot["difficulty"]), []).append(str(question_id))
    return {"experiment_id": experiment_id, "slots": slots, "approved": approved}


@pytest.fixture
def exam(subject, pool) -> dict:
    """An open exam on the pool's experiment: 10 minutes, 4 marks a question, 25% per hint."""
    config = {
        "experiment_ids": [pool["experiment_id"]],
        "slots": pool["slots"],
        "duration_minutes": 10,
        "marks_per_question": 4,
        "hint_penalty": 0.25,
    }
    with transaction() as conn:
        row = conn.execute(
            """
            insert into public.exams (teacher_id, subject_id, section, title, join_code, config, state)
            values (%s, %s, 'A', 'Test lab exam', %s, %s, 'open')
            returning id, join_code
            """,
            (TEACHER_ID, subject["id"], new_join_code(), Jsonb(config)),
        ).fetchone()
    return {"id": str(row["id"]), "join_code": row["join_code"], "experiment_id": pool["experiment_id"]}
