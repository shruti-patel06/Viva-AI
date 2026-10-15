"""The teacher's review endpoints and the audio job, against the local database.

The login check is replaced with a fixed teacher (dependency override), and Sarvam with a
fake, so these tests need no tokens and spend no API credits.
"""

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core import storage
from app.core.auth import CurrentUser, require_teacher
from app.core.config import get_settings
from app.core.db import transaction
from app.main import app
from app.modules.questions import audio_job
from tests.conftest import TEACHER_ID

OTHER_TEACHER = "44444444-0000-0000-0000-000000000002"


def login_as(teacher_id: str) -> TestClient:
    app.dependency_overrides[require_teacher] = lambda: CurrentUser(id=UUID(teacher_id), role="teacher")
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def question(subject) -> str:
    question_id = str(uuid4())
    with transaction() as conn:
        conn.execute(
            """
            insert into public.questions (id, teacher_id, experiment_id, type, difficulty, question_text,
                                          reference_answer, key_points, hints)
            values (%s, %s, %s, 'concept', 'easy', 'Why is a stack LIFO?', 'Last in, first out.',
                    '["newest leaves first"]', '["think of plates"]')
            """,
            (question_id, TEACHER_ID, subject["experiments"]["Stack Using Arrays"]),
        )
    return question_id


def row(question_id: str) -> dict:
    with transaction() as conn:
        return conn.execute("select * from public.questions where id = %s", (question_id,)).fetchone()


def audio_jobs(question_id: str) -> list[dict]:
    with transaction() as conn:
        return conn.execute(
            "select status from public.jobs where type = %s and payload->>'question_id' = %s",
            (audio_job.JOB_TYPE, question_id),
        ).fetchall()


class FakeVoice:
    def __init__(self):
        self.spoken: list[str] = []

    def __call__(self, text: str) -> bytes:
        self.spoken.append(text)
        return b"ID3 fake mp3 for " + text.encode()


@pytest.fixture
def voice(monkeypatch, subject) -> FakeVoice:
    fake = FakeVoice()
    monkeypatch.setattr(audio_job.speech, "synthesize", fake)
    real_upload = storage.upload

    def tracked_upload(bucket, path, content, content_type, overwrite=False):
        subject["stored"].append((bucket, path))
        real_upload(bucket, path, content, content_type, overwrite)

    monkeypatch.setattr(audio_job.storage, "upload", tracked_upload)
    return fake


def test_approving_queues_audio_once(question):
    client = login_as(TEACHER_ID)

    assert client.post(f"/questions/{question}/approve").status_code == 200

    assert row(question)["status"] == "approved"
    assert len(audio_jobs(question)) == 1


def test_another_teacher_cannot_touch_the_question(question):
    client = login_as(OTHER_TEACHER)

    assert client.post(f"/questions/{question}/approve").status_code == 404
    assert client.patch(f"/questions/{question}", json={"question_text": "Hacked?"}).status_code == 404
    assert row(question)["status"] == "draft"


def test_editing_sends_an_approved_question_back_to_draft_without_audio(question):
    with transaction() as conn:
        conn.execute(
            "update public.questions set status = 'approved', audio_path = 'old.mp3' where id = %s", (question,)
        )
    client = login_as(TEACHER_ID)

    response = client.patch(
        f"/questions/{question}", json={"question_text": "  Why does a stack pop the newest item?  ", "key_points": ["a", " "]}
    )

    assert response.status_code == 200
    edited = row(question)
    assert (edited["status"], edited["audio_path"]) == ("draft", None)
    assert edited["question_text"] == "Why does a stack pop the newest item?"
    assert edited["key_points"] == ["a"]


def test_edits_that_would_break_a_question_are_refused(question):
    client = login_as(TEACHER_ID)

    assert client.patch(f"/questions/{question}", json={}).status_code == 422
    assert client.patch(f"/questions/{question}", json={"question_text": "   "}).status_code == 422
    assert client.patch(f"/questions/{question}", json={"key_points": [" "]}).status_code == 422
    assert client.patch(f"/questions/{question}", json={"difficulty": "impossible"}).status_code == 422


def test_rejecting(question):
    assert login_as(TEACHER_ID).post(f"/questions/{question}/reject").status_code == 200
    assert row(question)["status"] == "rejected"


def test_audio_is_made_once_and_named_after_the_text(question, voice):
    login_as(TEACHER_ID).post(f"/questions/{question}/approve")

    audio_job.run({"question_id": question})
    audio_job.run({"question_id": question})  # a retry: the file is already there

    path = row(question)["audio_path"]
    assert path == audio_job.audio_path(TEACHER_ID, question, "Why is a stack LIFO?")
    assert voice.spoken == ["Why is a stack LIFO?"]
    assert storage.download(get_settings().audio_bucket, path).startswith(b"ID3")
    url = login_as(TEACHER_ID).get(f"/questions/{question}/audio").json()["url"]
    assert "/object/sign/question-audio/" in url


def test_edited_text_gets_new_audio(question, voice):
    client = login_as(TEACHER_ID)
    client.post(f"/questions/{question}/approve")
    audio_job.run({"question_id": question})
    first = row(question)["audio_path"]

    client.patch(f"/questions/{question}", json={"question_text": "What does LIFO mean?"})
    client.post(f"/questions/{question}/approve")
    audio_job.run({"question_id": question})

    assert row(question)["audio_path"] not in (None, first)
    assert voice.spoken == ["Why is a stack LIFO?", "What does LIFO mean?"]


def test_audio_is_skipped_for_a_question_no_longer_approved(question, voice):
    login_as(TEACHER_ID).post(f"/questions/{question}/approve")
    login_as(TEACHER_ID).post(f"/questions/{question}/reject")

    audio_job.run({"question_id": question})

    assert voice.spoken == []
    assert row(question)["audio_path"] is None
