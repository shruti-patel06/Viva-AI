"""Every AI call leaves a record, whatever its outcome, and recording never breaks a call."""

import httpx
import pytest

from app.ai import calls, llm, speech
from app.ai.rate_limit import RateLimiter
from app.core.db import transaction


def groq_replying(response: httpx.Response | Exception):
    def post(url, **kwargs):
        if isinstance(response, Exception):
            raise response
        response.request = httpx.Request("POST", url)
        return response

    return post


def ask(monkeypatch, response) -> None:
    monkeypatch.setattr(llm.httpx, "post", groq_replying(response))
    llm.GroqModel("key", "test-model", RateLimiter(30, 8000)).generate_json("s", "u", {}, "answer_score", max_tokens=100)


def test_a_successful_call_records_its_tokens(monkeypatch, ai_calls):
    body = {"choices": [{"message": {"content": "{}"}}], "usage": {"total_tokens": 812}}
    ask(monkeypatch, httpx.Response(200, json=body))

    (service, model, purpose, outcome, duration_ms, waited_ms, tokens, error), = ai_calls
    assert (service, model, purpose, outcome, tokens, error) == ("groq", "test-model", "answer_score", "ok", 812, None)
    assert duration_ms >= 0


def test_a_refused_call_is_recorded_as_rate_limited(monkeypatch, ai_calls):
    with pytest.raises(llm.RateLimited):
        ask(monkeypatch, httpx.Response(429, headers={"retry-after": "3"}))

    assert ai_calls[0][3] == "rate_limited"


def test_a_failed_call_is_recorded_with_its_error(monkeypatch, ai_calls):
    with pytest.raises(httpx.HTTPStatusError):
        ask(monkeypatch, httpx.Response(500, text="upstream exploded"))
    with pytest.raises(httpx.ConnectError):
        ask(monkeypatch, httpx.ConnectError("no route"))

    assert [(r[3], r[7]) for r in ai_calls] == [("error", "HTTP 500: upstream exploded"), ("error", "ConnectError: no route")]


def test_speech_to_text_failures_are_recorded(monkeypatch, ai_calls):
    monkeypatch.setattr(speech.httpx, "post", groq_replying(httpx.Response(429)))

    with pytest.raises(speech.SpeechUnavailable):
        speech.transcribe(b"audio", "audio/webm")

    assert ai_calls[0][:4] == ("sarvam", "saaras:v4", "stt", "rate_limited")


def test_recording_never_breaks_the_call(monkeypatch):
    def broken(*row):
        raise RuntimeError("database is down")

    monkeypatch.setattr(calls, "_save", broken)

    calls.record("groq", "m", "answer_score", "ok", 0.5)  # does not raise


def test_the_summary_counts_calls_by_purpose():
    rows = [("groq", "m", "test_summary", outcome, ms, 0, 900, None)
            for outcome, ms in [("ok", 1000), ("ok", 2000), ("rate_limited", 100)]]
    with transaction() as conn:
        for row in rows:
            conn.execute(
                """insert into public.ai_calls (service, model, purpose, outcome, duration_ms, waited_ms, tokens, error)
                   values (%s, %s, %s, %s, %s, %s, %s, %s)""",
                row,
            )
    try:
        mine = next(r for r in calls.summary(hours=1) if r["purpose"] == "test_summary")
        assert (mine["calls"], mine["ok"], mine["rate_limited"], mine["errors"], mine["tokens"]) == (3, 2, 1, 0, 2700)
    finally:
        with transaction() as conn:
            conn.execute("delete from public.ai_calls where purpose = 'test_summary'")
