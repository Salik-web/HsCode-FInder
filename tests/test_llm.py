"""LLM layer: provider error mapping, cache, fallback and tracing. No real network calls."""

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx2
import openai
import pytest
from pydantic import SecretStr

from app.config import MissingApiKeyError, ProviderConfig, Settings
from app.llm.cache import CachingClient, cache_key
from app.llm.client import (
    ImageInput,
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
    RateLimitedError,
    Usage,
)
from app.llm.fallback import FallbackClient
from app.llm.openai_compatible import OpenAICompatibleClient
from app.llm.tracing import TracingClient
from tests.fakes import FakeClient

SECRET_TEXT = "Tawkeel, Abaan and Gulshan, Plot 321"
BASE_URL = "https://llm.test/v1"
NO_WAIT = {"retry-after-ms": "1"}


def make_request(**changes: Any) -> LLMRequest:
    values: dict[str, Any] = {
        "system": "Extract the invoice.",
        "text": f"Text layer: {SECRET_TEXT}",
        "images": [ImageInput(media_type="image/png", data=b"\x89PNG fake page")],
        "response_schema": {"type": "object"},
        "schema_name": "RawInvoice",
        "document_sha256": "a" * 64,
        "prompt_version": "v1",
        "attempt": 1,
    }
    return LLMRequest(**(values | changes))


def make_response(content: str = '{"ok": true}', model: str = "model-a") -> LLMResponse:
    return LLMResponse(
        content=content,
        model=model,
        usage=Usage(input_tokens=1000, output_tokens=200),
        latency_ms=1234,
        cost_usd=Decimal("0.001"),
    )


# --- provider ------------------------------------------------------------------------------


def provider_config(**changes: Any) -> ProviderConfig:
    values: dict[str, Any] = {
        "name": "gemini",
        "base_url": BASE_URL,
        "api_key": SecretStr("test-key"),
        "model": "model-a",
        "input_usd_per_million_tokens": Decimal("0.30"),
        "output_usd_per_million_tokens": Decimal("2.50"),
        "reasoning_effort": None,
        "strict_schema": False,
        "tokens_per_minute": None,
        "requests_per_minute": None,
    }
    return ProviderConfig(**(values | changes))


def completion_body(content: str | None, finish_reason: str = "stop", usage: bool = True) -> dict:
    body: dict[str, Any] = {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "model-a-001",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {"role": "assistant", "content": content},
            }
        ],
    }
    if usage:
        body["usage"] = {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200}
    return body


def provider_with(
    handler: Any, max_retries: int = 0, config: ProviderConfig | None = None
) -> OpenAICompatibleClient:
    client = OpenAICompatibleClient(
        config or provider_config(), timeout_seconds=5, max_retries=max_retries, max_tokens=100
    )
    client._client = openai.OpenAI(
        api_key="test-key",
        base_url=BASE_URL,
        max_retries=max_retries,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    return client


def test_provider_parses_content_usage_and_cost() -> None:
    client = provider_with(lambda _: httpx2.Response(200, json=completion_body('{"a": 1}')))
    response = client.complete(make_request())
    assert response.content == '{"a": 1}'
    assert response.model == "model-a-001"
    assert response.usage == Usage(input_tokens=1000, output_tokens=200)
    # 1000 * 0.30 / 1e6 + 200 * 2.50 / 1e6
    assert response.cost_usd == Decimal("0.0008")
    assert response.cached is False


def test_provider_sends_schema_images_and_zero_temperature() -> None:
    sent: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=completion_body("{}"))

    provider_with(handler).complete(make_request())
    body = sent[0]
    assert body["temperature"] == 0
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "RawInvoice"
    image_part = body["messages"][1]["content"][1]
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")
    assert "reasoning_effort" not in body  # not configured, so the provider default applies
    assert "strict" not in body["response_format"]["json_schema"]


def test_strict_schema_is_sent_and_keyed_when_configured() -> None:
    sent: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=completion_body("{}"))

    client = provider_with(handler, config=provider_config(strict_schema=True))
    client.complete(make_request())
    assert sent[0]["response_format"]["json_schema"]["strict"] is True
    assert client.call_options == {"strict_schema": "true"}


def test_reasoning_effort_is_sent_when_configured() -> None:
    sent: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=completion_body("{}"))

    client = provider_with(handler, config=provider_config(reasoning_effort="none"))
    client.complete(make_request())
    assert sent[0]["reasoning_effort"] == "none"
    assert client.call_options == {"reasoning_effort": "none"}


def test_missing_usage_is_unknown_not_zero() -> None:
    client = provider_with(lambda _: httpx2.Response(200, json=completion_body("{}", usage=False)))
    response = client.complete(make_request())
    assert response.usage is None
    assert response.cost_usd is None


def test_rate_limit_is_retried_then_reported() -> None:
    calls: list[int] = []

    def handler(_: httpx2.Request) -> httpx2.Response:
        calls.append(1)
        return httpx2.Response(429, json={"error": {"message": "quota"}}, headers=NO_WAIT)

    with pytest.raises(RateLimitedError):
        provider_with(handler, max_retries=2).complete(make_request())
    assert len(calls) == 3


def test_rate_limit_then_success_returns_the_answer() -> None:
    responses = [
        httpx2.Response(429, json={"error": {"message": "slow down"}}, headers=NO_WAIT),
        httpx2.Response(200, json=completion_body("{}")),
    ]
    client = provider_with(lambda _: responses.pop(0), max_retries=1)
    assert client.complete(make_request()).content == "{}"


def test_timeout_maps_to_unavailable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("slow", request=request)

    with pytest.raises(LLMUnavailableError):
        provider_with(handler).complete(make_request())


def test_bad_request_maps_to_llm_error() -> None:
    client = provider_with(lambda _: httpx2.Response(400, json={"error": {"message": "bad"}}))
    with pytest.raises(LLMError, match="HTTP 400"):
        client.complete(make_request())


def test_truncated_output_is_an_error_not_partial_json() -> None:
    client = provider_with(lambda _: httpx2.Response(200, json=completion_body('{"a"', "length")))
    with pytest.raises(LLMError, match="token limit"):
        client.complete(make_request())


def test_empty_content_is_an_error() -> None:
    client = provider_with(lambda _: httpx2.Response(200, json=completion_body(None)))
    with pytest.raises(LLMError, match="no content"):
        client.complete(make_request())


# --- config --------------------------------------------------------------------------------


@pytest.mark.parametrize("provider", ["gemini", "openrouter", "groq"])
def test_missing_key_names_the_setting_to_add(provider: str) -> None:
    settings = Settings(_env_file=None, llm_provider=provider)
    with pytest.raises(MissingApiKeyError, match=f"{provider.upper()}_API_KEY"):
        settings.primary_provider()


def test_groq_is_the_default_with_qwen_and_thinking_off() -> None:
    settings = Settings(_env_file=None, groq_api_key=SecretStr("k"))
    primary = settings.primary_provider()
    fallback = settings.fallback_provider_config()
    assert primary.base_url == "https://api.groq.com/openai/v1"
    assert primary.model == "qwen/qwen3.8-27b"
    assert primary.reasoning_effort == "none"
    assert primary.strict_schema is True
    assert fallback is not None
    assert fallback.model == "qwen/qwen3.8-27b"
    assert fallback.reasoning_effort == "none"
    assert primary.tokens_per_minute is not None


def test_empty_settings_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_REASONING_EFFORT", "")
    monkeypatch.setenv("LLM_TOKENS_PER_MINUTE", "")
    primary = Settings(_env_file=None, groq_api_key=SecretStr("k")).primary_provider()
    assert primary.reasoning_effort is None
    assert primary.tokens_per_minute is None


def test_switching_provider_is_configuration_only() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="openrouter",
        llm_model="some/model:free",
        openrouter_api_key=SecretStr("k"),
    )
    config = settings.primary_provider()
    assert config.base_url == "https://openrouter.ai/api/v1"
    assert config.model == "some/model:free"


def test_no_fallback_configured_gives_none() -> None:
    assert Settings(_env_file=None, fallback_provider=None).fallback_provider_config() is None


# --- cache ---------------------------------------------------------------------------------


def test_cache_key_is_stable() -> None:
    assert cache_key(make_request(), "m", {}) == cache_key(make_request(), "m", {})


@pytest.mark.parametrize(
    "change",
    [
        {"document_sha256": "b" * 64},
        {"prompt_version": "v2"},
        {"attempt": 2},
        {"text": "different text layer"},
        {"images": []},
    ],
)
def test_cache_key_changes_with_any_input(change: dict) -> None:
    assert cache_key(make_request(**change), "m", {}) != cache_key(make_request(), "m", {})


def test_cache_key_changes_with_call_options() -> None:
    thinking_off = cache_key(make_request(), "m", {"reasoning_effort": "none"})
    assert thinking_off != cache_key(make_request(), "m", {})
    assert thinking_off != cache_key(make_request(), "m", {"reasoning_effort": "default"})


def test_cache_key_without_options_matches_the_original_formula() -> None:
    """Regression: adding call options must not orphan the Gemini baseline's cache."""
    request = make_request()
    digest = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
    parts = [request.document_sha256, "m", request.prompt_version, request.attempt, digest]
    original = hashlib.sha256(json.dumps(parts).encode()).hexdigest()
    assert cache_key(request, "m", {}) == original


def test_cache_reads_entries_written_before_call_options(tmp_path: Path) -> None:
    client = CachingClient(FakeClient("model-a", [make_response()]), tmp_path, {})
    client.complete(make_request())
    (path,) = tmp_path.rglob("*.json")
    entry = json.loads(path.read_text())
    del entry["call_options"]
    path.write_text(json.dumps(entry))
    assert CachingClient(FakeClient("model-a", []), tmp_path, {}).complete(make_request()).cached


def test_cache_key_changes_with_model() -> None:
    assert cache_key(make_request(), "m1", {}) != cache_key(make_request(), "m2", {})


def test_second_call_is_served_from_disk(tmp_path: Path) -> None:
    inner = FakeClient("model-a", [make_response()])
    client = CachingClient(inner, tmp_path, {})
    first = client.complete(make_request())
    second = client.complete(make_request())
    assert len(inner.requests) == 1
    assert first.cached is False
    assert second.cached is True
    assert second.model_dump(exclude={"cached"}) == first.model_dump(exclude={"cached"})


def test_cache_survives_a_new_client_instance(tmp_path: Path) -> None:
    CachingClient(FakeClient("model-a", [make_response()]), tmp_path, {}).complete(make_request())
    fresh_inner = FakeClient("model-a", [])
    assert CachingClient(fresh_inner, tmp_path, {}).complete(make_request()).cached
    assert fresh_inner.requests == []


def test_failures_are_not_cached(tmp_path: Path) -> None:
    inner = FakeClient("model-a", [RateLimitedError("quota"), make_response()])
    client = CachingClient(inner, tmp_path, {})
    with pytest.raises(RateLimitedError):
        client.complete(make_request())
    assert client.complete(make_request()).cached is False
    assert len(inner.requests) == 2


def test_cache_leaves_no_temporary_files(tmp_path: Path) -> None:
    CachingClient(FakeClient("model-a", [make_response()]), tmp_path, {}).complete(make_request())
    files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert len(files) == 1
    assert files[0].suffix == ".json"


# --- fallback ------------------------------------------------------------------------------


def test_fallback_answers_when_primary_is_rate_limited() -> None:
    primary = FakeClient("model-a", [RateLimitedError("quota")])
    fallback = FakeClient("model-b", [make_response(model="model-b")])
    response = FallbackClient(primary, fallback).complete(make_request())
    assert response.model == "model-b"
    assert len(primary.requests) == len(fallback.requests) == 1


def test_fallback_is_unused_when_primary_answers() -> None:
    fallback = FakeClient("model-b", [])
    FallbackClient(FakeClient("model-a", [make_response()]), fallback).complete(make_request())
    assert fallback.requests == []


@pytest.mark.parametrize("error", [LLMError("bad request"), LLMUnavailableError("timeout")])
def test_other_errors_do_not_switch_models(error: LLMError) -> None:
    fallback = FakeClient("model-b", [])
    with pytest.raises(type(error)):
        FallbackClient(FakeClient("model-a", [error]), fallback).complete(make_request())
    assert fallback.requests == []


def test_both_rate_limited_propagates_the_rate_limit() -> None:
    primary = FakeClient("model-a", [RateLimitedError("quota")])
    fallback = FakeClient("model-b", [RateLimitedError("quota")])
    with pytest.raises(RateLimitedError):
        FallbackClient(primary, fallback).complete(make_request())


# --- tracing -------------------------------------------------------------------------------


class FakeObservation:
    def __init__(self, fields: dict[str, Any]) -> None:
        self.fields = fields
        self.ended = False

    def update(self, **fields: Any) -> None:
        self.fields |= fields

    def end(self) -> None:
        self.ended = True


class FakeTracer:
    def __init__(self) -> None:
        self.observations: list[FakeObservation] = []

    def start_observation(self, **fields: Any) -> FakeObservation:
        observation = FakeObservation(fields)
        self.observations.append(observation)
        return observation


def test_trace_records_metadata() -> None:
    tracer = FakeTracer()
    TracingClient(FakeClient("model-a", [make_response()]), tracer).complete(make_request())
    (observation,) = tracer.observations
    assert observation.ended
    assert observation.fields["as_type"] == "generation"
    assert observation.fields["version"] == "v1"
    assert observation.fields["usage_details"] == {"input": 1000, "output": 200}
    assert observation.fields["cost_details"] == {"total": 0.001}
    assert observation.fields["input"]["image_count"] == 1


def test_trace_never_contains_document_content() -> None:
    tracer = FakeTracer()
    response = make_response(content=json.dumps({"buyer": SECRET_TEXT}))
    TracingClient(FakeClient("model-a", [response]), tracer).complete(make_request())
    traced = repr(tracer.observations[0].fields)
    assert SECRET_TEXT not in traced
    assert "fake page" not in traced
    assert "Extract the invoice" not in traced


def test_failed_call_is_traced_as_an_error_and_reraised() -> None:
    tracer = FakeTracer()
    client = TracingClient(FakeClient("model-a", [RateLimitedError("quota")]), tracer)
    with pytest.raises(RateLimitedError):
        client.complete(make_request())
    (observation,) = tracer.observations
    assert observation.fields["level"] == "ERROR"
    assert observation.fields["status_message"] == "RateLimitedError"
    assert observation.ended
