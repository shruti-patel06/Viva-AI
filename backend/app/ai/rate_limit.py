"""Pacing calls to an AI service so its per-minute limits are not exceeded.

Two token buckets, one counting requests and one counting tokens. Each holds up to a
minute's allowance and refills continuously, which is also how Groq counts (its
x-ratelimit-reset-tokens header is the time to refill what was just used). Before a call
the worker waits until both buckets have room, so a burst of answers is spread out at the
rate the service allows, instead of being sent at once and refused.

The buckets live in the worker process, so they are exact for one worker. After each call
the token bucket is corrected from the provider's own count where it reports one, which also
accounts for anything else using the same key.
"""

import threading
import time
from collections.abc import Callable

MIN_WAIT_SECONDS = 0.001


class RateLimiter:
    def __init__(
        self,
        requests_per_minute: int,
        tokens_per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.requests_per_minute = requests_per_minute
        self.tokens_per_minute = tokens_per_minute
        self._requests = float(requests_per_minute)
        self._tokens = float(tokens_per_minute)
        self._clock, self._sleep = clock, sleep
        self._updated = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        minutes = (now - self._updated) / 60
        self._requests = min(self.requests_per_minute, self._requests + self.requests_per_minute * minutes)
        self._tokens = min(self.tokens_per_minute, self._tokens + self.tokens_per_minute * minutes)
        self._updated = now

    def acquire(self, tokens: int) -> float:
        """Wait until one request of about `tokens` tokens fits, then take it. Returns the
        seconds spent waiting."""
        # A call bigger than a whole minute's allowance could otherwise never start.
        tokens = min(tokens, self.tokens_per_minute)
        waited = 0.0
        with self._lock:
            while True:
                self._refill()
                wait = max(
                    (1 - self._requests) / self.requests_per_minute * 60,
                    (tokens - self._tokens) / self.tokens_per_minute * 60,
                )
                # Rounding can leave a bucket a hair short after a wait. Under a millisecond
                # counts as ready, or the loop could sleep in steps too small to move the clock.
                if wait < MIN_WAIT_SECONDS:
                    break
                self._sleep(wait)
                waited += wait
            self._requests -= 1
            self._tokens -= tokens
        return waited

    def settle(self, estimated: int, used: int, remaining: int | None = None) -> None:
        """After a call: replace the estimate with what the call really used, and if the
        provider said how many tokens are left this minute, trust that when it is lower."""
        with self._lock:
            self._refill()
            self._tokens -= used - min(estimated, self.tokens_per_minute)
            if remaining is not None:
                self._tokens = min(self._tokens, remaining)
