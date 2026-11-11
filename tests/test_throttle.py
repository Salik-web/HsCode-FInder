"""Throttling with a fake clock: no real waiting, exact waits asserted."""

import threading
from decimal import Decimal

import pytest

from app.llm.client import LLMResponse, RateLimitedError, Usage
from app.llm.throttle import (
    INITIAL_OUTPUT_ESTIMATE,
    INITIAL_TOKEN_ESTIMATE,
    WINDOW_SECONDS,
    ThrottledClient,
)
from tests.fakes import FakeClient
from tests.test_llm import make_request


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def response(total_tokens: int | None, output_tokens: int = 0) -> LLMResponse:
    usage = None
    if total_tokens is not None:
        usage = Usage(input_tokens=total_tokens - output_tokens, output_tokens=output_tokens)
    return LLMResponse(content="{}", model="m", usage=usage, latency_ms=1, cost_usd=Decimal(0))


def throttled(
    results: list, tokens: int | None, requests: int | None, clock: FakeClock
) -> ThrottledClient:
    return ThrottledClient(
        FakeClient("m", results), tokens, requests, clock=clock, sleep=clock.sleep
    )


def test_no_limits_never_waits() -> None:
    clock = FakeClock()
    client = throttled([response(9_000)] * 5, None, None, clock)
    for _ in range(5):
        client.complete(make_request())
    assert clock.sleeps == []


def test_calls_within_budget_do_not_wait() -> None:
    clock = FakeClock()
    client = throttled([response(1_000)] * 3, 10_000, 30, clock)
    for _ in range(3):
        client.complete(make_request())
    assert clock.sleeps == []


def test_token_budget_waits_until_the_oldest_call_leaves_the_window() -> None:
    clock = FakeClock()
    client = throttled([response(3_000), response(3_000), response(3_000)], 6_000, None, clock)
    client.complete(make_request())  # t=0: books the 4,000 estimate, then 3,000 actual
    clock.now += 10
    client.complete(make_request())  # t=10: 3,000 + estimate 4,000 > 6,000 -> waits
    assert clock.sleeps == [pytest.approx(WINDOW_SECONDS - 10)]
    # The second call started at t=60. At t=61 it is still in the window, so the third
    # call waits until it leaves at t=120.
    clock.now += 1
    client.complete(make_request())
    assert clock.sleeps[1] == pytest.approx(WINDOW_SECONDS - 1)


def test_request_budget_waits_for_the_oldest_request() -> None:
    clock = FakeClock()
    client = throttled([response(10)] * 3, None, 2, clock)
    client.complete(make_request())
    clock.now += 5
    client.complete(make_request())
    client.complete(make_request())
    assert clock.sleeps == [pytest.approx(WINDOW_SECONDS - 5)]


def test_estimate_grows_to_the_largest_call_seen() -> None:
    clock = FakeClock()
    client = throttled([response(5_000), response(100), response(100)], 12_000, None, clock)
    client.complete(make_request())  # 5,000 actual becomes the estimate
    client.complete(make_request())  # 5,000 + 5,000 <= 12,000: no wait
    assert clock.sleeps == []
    client.complete(make_request())  # 5,100 + 5,000 <= 12,000: still no wait
    assert clock.sleeps == []


def test_first_call_uses_the_initial_estimate() -> None:
    clock = FakeClock()
    client = throttled([response(None), response(None)], INITIAL_TOKEN_ESTIMATE + 1, None, clock)
    client.complete(make_request())
    client.complete(make_request())  # unknown usage keeps the 4,000 booking: must wait
    assert clock.sleeps == [pytest.approx(WINDOW_SECONDS)]


def test_call_bigger_than_the_whole_budget_waits_for_an_empty_window() -> None:
    clock = FakeClock()
    client = throttled([response(20_000), response(20_000)], 6_000, None, clock)
    client.complete(make_request())
    client.complete(make_request())
    assert clock.sleeps == [pytest.approx(WINDOW_SECONDS)]


def test_failed_call_keeps_its_booking() -> None:
    clock = FakeClock()
    client = throttled([RateLimitedError("quota"), response(10)], 5_000, None, clock)
    with pytest.raises(RateLimitedError):
        client.complete(make_request())
    client.complete(make_request())  # the failed call's 4,000 still counts
    assert clock.sleeps == [pytest.approx(WINDOW_SECONDS)]


def test_concurrent_callers_never_exceed_the_request_budget() -> None:
    clock = FakeClock()
    lock = threading.Lock()

    def sleep(seconds: float) -> None:
        with lock:
            clock.sleep(seconds)

    client = ThrottledClient(FakeClient("m", [response(1)] * 6), None, 2, clock=clock, sleep=sleep)
    threads = [threading.Thread(target=client.complete, args=(make_request(),)) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert clock.now - 1000.0 == pytest.approx(2 * WINDOW_SECONDS)


def output_throttled(results: list, clock: FakeClock) -> ThrottledClient:
    return ThrottledClient(
        FakeClient("m", results), None, None, 1_000, clock=clock, sleep=clock.sleep
    )


def test_output_budget_allows_calls_that_fit_together() -> None:
    clock = FakeClock()
    client = output_throttled([response(3_000, 400), response(3_000, 400)], clock)
    client.complete(make_request())  # books 800, then 400 actual
    client.complete(make_request())  # 400 + estimate 800 > 1,000 -> waits
    assert clock.sleeps == [pytest.approx(WINDOW_SECONDS)]


def test_output_estimate_never_drops_below_the_initial_estimate() -> None:
    clock = FakeClock()
    small = [response(3_000, 300), response(3_000, 300), response(3_000, 300)]
    client = ThrottledClient(
        FakeClient("m", small), None, None, 1_000, clock=clock, sleep=clock.sleep
    )
    assert INITIAL_OUTPUT_ESTIMATE > 300
    client.complete(make_request())
    clock.now += WINDOW_SECONDS  # empty window; the estimate is still the initial 800
    client.complete(make_request())
    client.complete(make_request())  # 300 + 800 > 1,000: waits
    assert len(clock.sleeps) == 1


def test_output_budget_is_independent_of_the_total_budget() -> None:
    clock = FakeClock()
    client = ThrottledClient(
        FakeClient("m", [response(500, 450), response(500, 450)]),
        100_000,
        None,
        1_000,
        clock=clock,
        sleep=clock.sleep,
    )
    client.complete(make_request())
    client.complete(make_request())  # total is tiny, but 450 + 800 output > 1,000
    assert clock.sleeps == [pytest.approx(WINDOW_SECONDS)]
