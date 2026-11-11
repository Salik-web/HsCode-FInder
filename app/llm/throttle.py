"""Client-side throttling under per-minute token and request limits.

Waiting before a call is cheaper than being refused and retried: a refused call still
counts against some providers' limits, and retries add latency at random.
"""

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from app.llm.client import LLMClient, LLMRequest, LLMResponse

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60.0
# A call's size is unknown until it returns; until one has, assume a typical invoice call.
INITIAL_TOKEN_ESTIMATE = 4_000


@dataclass
class _Spend:
    started: float
    tokens: int


class ThrottledClient:
    """Waits until a call fits in the last minute's token and request budget.

    The next call is assumed to be as large as the largest call so far, which errs on
    the side of waiting. A call larger than the whole budget waits for an empty window.
    """

    def __init__(
        self,
        inner: LLMClient,
        tokens_per_minute: int | None,
        requests_per_minute: int | None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._inner = inner
        self._tokens_per_minute = tokens_per_minute
        self._requests_per_minute = requests_per_minute
        self._clock = clock
        self._sleep = sleep
        self._window: deque[_Spend] = deque()
        self._largest_call = INITIAL_TOKEN_ESTIMATE
        self._lock = threading.Lock()

    @property
    def model(self) -> str:
        return self._inner.model

    def complete(self, request: LLMRequest) -> LLMResponse:
        spend = self._reserve()
        # On failure the estimate stays booked: the provider may have counted the call.
        response = self._inner.complete(request)
        if response.usage is not None:
            actual = response.usage.input_tokens + response.usage.output_tokens
            with self._lock:
                spend.tokens = actual
                self._largest_call = max(self._largest_call, actual)
        return response

    def _reserve(self) -> _Spend:
        # Held while sleeping, so waiting callers go one at a time in arrival order.
        with self._lock:
            estimate = self._largest_call
            if self._tokens_per_minute is not None:
                estimate = min(estimate, self._tokens_per_minute)
            while True:
                now = self._clock()
                self._forget_before(now - WINDOW_SECONDS)
                wait = self._wait_for(now, estimate)
                if wait <= 0:
                    break
                logger.info("throttling %.1f s to stay under per-minute limits", wait)
                self._sleep(wait)
            spend = _Spend(started=now, tokens=estimate)
            self._window.append(spend)
            return spend

    def _forget_before(self, cutoff: float) -> None:
        while self._window and self._window[0].started <= cutoff:
            self._window.popleft()

    def _wait_for(self, now: float, estimate: int) -> float:
        """Seconds until enough old calls leave the window for this one to fit."""
        waits = [0.0]
        limit = self._requests_per_minute
        if limit is not None and len(self._window) >= limit:
            waits.append(self._window[len(self._window) - limit].started + WINDOW_SECONDS - now)
        if self._tokens_per_minute is not None:
            excess = sum(spend.tokens for spend in self._window) + estimate
            excess -= self._tokens_per_minute
            for spend in self._window:
                if excess <= 0:
                    break
                excess -= spend.tokens
                waits.append(spend.started + WINDOW_SECONDS - now)
        return max(waits)
