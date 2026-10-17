"""The stage 5 self-check: 60 answers arrive in a minute and the AI service allows fewer calls.

GroqModel is run against a simulated Groq that refuses (429) any call its per-minute token
bucket cannot cover, on a fake clock, so ten minutes of pacing take a fraction of a second.
"""

import json

import httpx
import pytest

from app.ai import llm
from app.ai.rate_limit import RateLimiter
from tests.test_rate_limit import FakeClock

TOKENS_PER_CALL = 900  # what a real scoring call used when measured against Groq


class SimulatedGroq:
    def __init__(self, clock: FakeClock, tokens_per_minute: int = 8000):
        self.clock, self.tokens_per_minute = clock, tokens_per_minute
        self.level, self.updated = float(tokens_per_minute), clock.now
        self.served = self.refused = 0

    def post(self, url, headers, json, timeout):  # noqa: A002 - same signature as httpx.post
        self.level = min(self.tokens_per_minute, self.level + (self.clock.now - self.updated) * self.tokens_per_minute / 60)
        self.updated = self.clock.now
        request = httpx.Request("POST", url)
        if self.level < TOKENS_PER_CALL:
            self.refused += 1
            return httpx.Response(429, headers={"retry-after": "5"}, request=request)
        self.level -= TOKENS_PER_CALL
        self.served += 1
        self.clock.now += 0.7  # a call takes about this long
        body = {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {"total_tokens": TOKENS_PER_CALL}}
        return httpx.Response(200, json=body, headers={"x-ratelimit-remaining-tokens": str(int(self.level))}, request=request)


def burst(monkeypatch, limiter: RateLimiter, groq: SimulatedGroq, calls: int = 60) -> None:
    monkeypatch.setattr(llm.httpx, "post", groq.post)
    model = llm.GroqModel("test-key", "test-model", limiter)
    system, user = "s" * 2000, "u" * 500  # about the size of the scoring prompt
    for _ in range(calls):
        try:
            model.generate_json(system, user, {}, "answer_score", max_tokens=1000)
        except llm.RateLimited:
            pass  # in the worker this job would be postponed; here we only count refusals


def test_a_burst_of_60_scoring_calls_is_spread_out_and_never_refused(monkeypatch):
    clock = FakeClock()
    groq = SimulatedGroq(clock)
    limiter = RateLimiter(30, 8000, clock=clock.time, sleep=clock.sleep)

    burst(monkeypatch, limiter, groq)

    assert (groq.served, groq.refused) == (60, 0)
    # 60 x 900 tokens at 8,000 a minute, less the first minute's allowance: under 6 minutes.
    expected_seconds = (60 * TOKENS_PER_CALL - 8000) / 8000 * 60
    assert clock.now == pytest.approx(expected_seconds, rel=0.1)


def test_without_pacing_the_same_burst_is_mostly_refused(monkeypatch):
    clock = FakeClock()
    groq = SimulatedGroq(clock)
    unlimited = RateLimiter(10_000, 10_000_000, clock=clock.time, sleep=clock.sleep)

    burst(monkeypatch, unlimited, groq)

    assert groq.refused > 40
