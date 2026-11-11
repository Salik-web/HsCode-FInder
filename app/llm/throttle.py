"""Client-side throttling under per-minute token, output-token and request limits.

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
# A compact-format invoice of 15 lines writes about 700 output tokens.
INITIAL_OUTPUT_ESTIMATE = 800


@dataclass
class _Spend:
    started: float
    tokens: int  # input + output
    output_tokens: int


class ThrottledClient:
    """Waits until a call fits in the last minute's budgets.

    The next call is assumed to be as large as the largest call so far, in total and in
    output tokens, which errs on the side of waiting. The number of lines, which drives
    the output size, is only known after the call, so the largest call seen is the best
    available estimate. A call larger than a whole budget waits for an empty window.
    """

    def __init__(
        self,
        inner: LLMClient,
        tokens_per_minute: int | None,
        requests_per_minute: int | None,
        output_tokens_per_minute: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._inner = inner
        self._tokens_per_minute = tokens_per_minute
        self._requests_per_minute = requests_per_minute
        self._output_tokens_per_minute = output_tokens_per_minute
        self._clock = clock
        self._sleep = sleep
        self._window: deque[_Spend] = deque()
        self._largest_call = INITIAL_TOKEN_ESTIMATE
        self._largest_output = INITIAL_OUTPUT_ESTIMATE
        self._lock = threading.Lock()

    @property
    def model(self) -> str:
        return self._inner.model

    def complete(self, request: LLMRequest) -> LLMResponse:
        spend = self._reserve()
        # On failure the estimate stays booked: the provider may have counted the call.
        response = self._inner.complete(request)
        if response.usage is not None:
            output = response.usage.output_tokens
            total = response.usage.input_tokens + output
            with self._lock:
                spend.tokens = total
                spend.output_tokens = output
                self._largest_call = max(self._largest_call, total)
                self._largest_output = max(self._largest_output, output)
        return response

    def _reserve(self) -> _Spend:
        # Held while sleeping, so waiting callers go one at a time in arrival order.
        with self._lock:
            estimate = _Spend(
                started=0.0,
                tokens=_capped(self._largest_call, self._tokens_per_minute),
                output_tokens=_capped(self._largest_output, self._output_tokens_per_minute),
            )
            while True:
                now = self._clock()
                self._forget_before(now - WINDOW_SECONDS)
                wait = self._wait_for(now, estimate)
                if wait <= 0:
                    break
                logger.info("throttling %.1f s to stay under per-minute limits", wait)
                self._sleep(wait)
            estimate.started = now
            self._window.append(estimate)
            return estimate

    def _forget_before(self, cutoff: float) -> None:
        while self._window and self._window[0].started <= cutoff:
            self._window.popleft()

    def _wait_for(self, now: float, estimate: _Spend) -> float:
        """Seconds until enough old calls leave the window for this one to fit."""
        waits = [0.0]
        limit = self._requests_per_minute
        if limit is not None and len(self._window) >= limit:
            waits.append(self._window[len(self._window) - limit].started + WINDOW_SECONDS - now)
        waits.append(self._budget_wait(now, self._tokens_per_minute, estimate.tokens, "tokens"))
        waits.append(
            self._budget_wait(
                now, self._output_tokens_per_minute, estimate.output_tokens, "output_tokens"
            )
        )
        return max(waits)

    def _budget_wait(self, now: float, limit: int | None, needed: int, field: str) -> float:
        if limit is None:
            return 0.0
        excess = sum(getattr(spend, field) for spend in self._window) + needed - limit
        wait = 0.0
        for spend in self._window:
            if excess <= 0:
                break
            excess -= getattr(spend, field)
            wait = spend.started + WINDOW_SECONDS - now
        return wait


def _capped(estimate: int, limit: int | None) -> int:
    return estimate if limit is None else min(estimate, limit)
