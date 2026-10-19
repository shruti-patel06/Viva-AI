"""Spoken answers and question audio, with the speech service replaced by stand-ins.

The point of stage 6 is that losing speech loses a feature, not the viva: every failure
here must leave the student able to type and submit.
"""

import httpx
import pytest

from app.ai import speech
from app.core.db import transaction
from app.modules.sessions import router as sessions_router


@pytest.fixture
def viva(login, students, exam):
    client = login(students[0])
    session_id = client.post(
        "/sessions", json={"join_code": exam["join_code"], "roll_number": "22CS001", "experiment_id": exam["experiment_id"]}
    ).json()["id"]
    client.post(f"/sessions/{session_id}/start")
    return client, session_id


def record(client, session_id, position=1, audio=b"OggS fake opus"):
    return client.post(
        f"/sessions/{session_id}/transcribe",
        data={"position": str(position)},
        files={"recording": ("answer.webm", audio, "audio/webm")},
    )


def submit(client, session_id, text, position=1):
    return client.post(
        f"/sessions/{session_id}/answers",
        json={"position": position, "text": text, "idempotency_key": f"key-{session_id}-{position}"},
    )


def saved_answer(session_id, position=1) -> dict:
    with transaction() as conn:
        return conn.execute(
            """
            select a.text, a.spoken_transcript, a.audio_path from public.answers a
            join public.session_questions sq on sq.id = a.session_question_id
            where sq.session_id = %s and sq.position = %s
            """,
            (session_id, position),
        ).fetchone()


class FakeSpeech:
    def __init__(self, *heard: str, error: Exception | None = None):
        self.heard, self.error, self.calls = list(heard), error, 0

    def __call__(self, audio: bytes, content_type: str) -> str:
        self.calls += 1
        if self.error:
            raise self.error
        return self.heard.pop(0)


def test_a_recording_becomes_text_the_student_checks_before_submitting(viva, monkeypatch):
    client, session_id = viva
    monkeypatch.setattr(speech, "transcribe", FakeSpeech("a stack is last in fist out"))

    heard = record(client, session_id)
    # The student reads the transcript, fixes "fist", and submits.
    submit(client, session_id, "a stack is last in first out")

    assert heard.json() == {"transcript": "a stack is last in fist out"}
    answer = saved_answer(session_id)
    assert answer["text"] == "a stack is last in first out"
    assert answer["spoken_transcript"] == "a stack is last in fist out"  # kept to measure speech quality
    assert answer["audio_path"] is None  # the recording itself is not stored


def test_a_long_answer_can_be_recorded_in_parts(viva, monkeypatch):
    client, session_id = viva
    monkeypatch.setattr(speech, "transcribe", FakeSpeech("push adds at the top", "and pop removes from the top"))

    record(client, session_id)
    record(client, session_id)
    submit(client, session_id, "push adds at the top and pop removes from the top")

    assert saved_answer(session_id)["spoken_transcript"] == "push adds at the top and pop removes from the top"


@pytest.mark.parametrize(
    "failure",
    [
        speech.SpeechUnavailable("The speech service is busy (too many requests)"),
        speech.SpeechUnavailable("Could not reach the speech service: ConnectTimeout"),
    ],
)
def test_when_speech_fails_the_student_is_told_to_type_and_the_viva_goes_on(viva, monkeypatch, failure):
    client, session_id = viva
    monkeypatch.setattr(speech, "transcribe", FakeSpeech(error=failure))

    response = record(client, session_id)

    assert response.status_code == 503
    assert "Type your answer" in response.json()["detail"]
    view = client.get(f"/sessions/{session_id}").json()
    assert (view["state"], view["question"]["position"]) == ("in_progress", 1)
    # Typing still works, and the answer is recorded as typed.
    assert submit(client, session_id, "typed instead").status_code == 200
    assert saved_answer(session_id) == {"text": "typed instead", "spoken_transcript": None, "audio_path": None}


def test_a_recording_is_only_taken_for_the_current_question(viva, monkeypatch, login, students):
    client, session_id = viva
    fake = FakeSpeech("anything")
    monkeypatch.setattr(speech, "transcribe", fake)

    assert record(client, session_id, position=2).status_code == 409
    assert record(login(students[1]), session_id).status_code == 404  # someone else's viva
    assert record(client, session_id, audio=b"").status_code == 422
    assert fake.calls == 0  # refused before the speech service is called


def test_a_recording_that_is_too_large_is_refused(viva, monkeypatch):
    client, session_id = viva
    monkeypatch.setattr(sessions_router, "MAX_RECORDING_BYTES", 10)
    monkeypatch.setattr(speech, "transcribe", FakeSpeech("anything"))

    assert record(client, session_id, audio=b"x" * 11).status_code == 413


def test_question_audio_is_a_short_lived_link(viva, monkeypatch):
    client, session_id = viva
    assert client.get(f"/sessions/{session_id}/audio").status_code == 404  # no audio made yet

    with transaction() as conn:
        conn.execute(
            """
            update public.questions set audio_path = 'teacher/question.mp3'
            where id = (select question_id from public.session_questions where session_id = %s and position = 1)
            """,
            (session_id,),
        )
    monkeypatch.setattr(sessions_router.storage, "signed_url", lambda bucket, path, expires_in_seconds: f"signed:{bucket}/{path}")

    assert client.get(f"/sessions/{session_id}/audio").json() == {"url": "signed:question-audio/teacher/question.mp3"}


def fake_post(response: httpx.Response | Exception):
    def post(*args, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response

    return post


def sarvam_reply(status_code: int, body: dict | None = None) -> httpx.Response:
    return httpx.Response(status_code, json=body or {}, request=httpx.Request("POST", speech.SARVAM_STT_URL))


def test_transcribe_returns_sarvams_transcript(monkeypatch):
    monkeypatch.setattr(speech.httpx, "post", fake_post(sarvam_reply(200, {"transcript": " a stack is lifo "})))

    assert speech.transcribe(b"audio", "audio/webm") == "a stack is lifo"


def test_the_browsers_codec_label_is_dropped_before_sending(monkeypatch):
    sent = {}

    def post(url, headers, files, data, timeout):
        sent["type"] = files["file"][2]
        return sarvam_reply(200, {"transcript": "ok"})

    monkeypatch.setattr(speech.httpx, "post", post)
    speech.transcribe(b"audio", "audio/webm;codecs=opus")

    assert sent["type"] == "audio/webm"  # Sarvam refuses "audio/webm;codecs=opus"


@pytest.mark.parametrize(
    "outcome",
    [sarvam_reply(429), sarvam_reply(503), sarvam_reply(403), httpx.ConnectTimeout("slow"), httpx.ConnectError("down")],
)
def test_every_speech_service_failure_becomes_speech_unavailable(monkeypatch, outcome):
    monkeypatch.setattr(speech.httpx, "post", fake_post(outcome))

    with pytest.raises(speech.SpeechUnavailable):
        speech.transcribe(b"audio", "audio/webm")
