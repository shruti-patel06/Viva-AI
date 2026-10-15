from uuid import uuid4

import pytest

from app.core import storage
from app.core.config import get_settings
from app.core.db import close_pool, transaction
from tests.sample_manual import EXPERIMENTS

TEACHER_ID = "44444444-0000-0000-0000-000000000001"


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
