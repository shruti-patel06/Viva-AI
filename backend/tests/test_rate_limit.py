"""The rate limiter with a fake clock, so a minute of pacing runs instantly and exactly."""

import pytest

from app.ai.rate_limit import RateLimiter


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def limiter(requests_per_minute: int, tokens_per_minute: int) -> tuple[RateLimiter, FakeClock]:
    clock = FakeClock()
    return RateLimiter(requests_per_minute, tokens_per_minute, clock=clock.time, sleep=clock.sleep), clock


def test_calls_inside_the_limits_go_straight_away():
    rate, clock = limiter(30, 8000)

    waits = [rate.acquire(900) for _ in range(8)]  # 7,200 tokens

    assert waits == [0] * 8
    assert clock.now == 0


def test_a_burst_is_spread_out_at_the_token_rate():
    rate, clock = limiter(30, 8000)

    for _ in range(60):
        rate.acquire(1000)

    # The first 8 fit in a full minute's allowance; each of the other 52 waits for 1,000
    # tokens to refill at 8,000 a minute, which is 7.5 seconds.
    assert clock.now == pytest.approx(52 * 7.5)


def test_requests_per_minute_also_limits():
    rate, clock = limiter(10, 1_000_000)

    for _ in range(25):
        rate.acquire(1)

    assert clock.now == pytest.approx(15 * 6)  # 10 at once, then one every 6 seconds


def test_an_overestimate_is_given_back_after_the_call():
    rate, clock = limiter(30, 8000)
    rate.acquire(8000)

    rate.settle(estimated=8000, used=2000)

    assert rate.acquire(6000) == 0


def test_the_providers_own_count_wins_when_it_is_lower():
    rate, clock = limiter(30, 8000)
    rate.acquire(1000)

    rate.settle(estimated=1000, used=1000, remaining=0)  # something else used the key too

    assert rate.acquire(800) == pytest.approx(6)  # 800 tokens refill in 6 seconds


def test_a_call_bigger_than_a_minute_of_tokens_can_still_run():
    rate, clock = limiter(30, 8000)

    assert rate.acquire(20_000) == 0
    assert rate.acquire(20_000) == pytest.approx(60)
