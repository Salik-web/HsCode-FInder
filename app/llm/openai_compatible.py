"""The one provider implementation: any OpenAI-compatible chat endpoint."""

import base64
import time
from decimal import Decimal
from typing import Any

import openai

from app.config import ProviderConfig
from app.llm.client import (
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
    RateLimitedError,
    RequestTooLargeError,
    SchemaViolationError,
    Usage,
)

TOKENS_PER_MILLION = Decimal(1_000_000)
MS_PER_SECOND = 1000
# Groq's 429 for a request that exceeds a per-minute limit on its own: no wait fixes it.
TOO_LARGE_MARKER = "request too large"
# Groq's 400 when strict mode rejects the generated JSON.
SCHEMA_REJECTED_CODE = "json_validate_failed"


class OpenAICompatibleClient:
    def __init__(
        self, provider: ProviderConfig, timeout_seconds: float, max_retries: int, max_tokens: int
    ) -> None:
        self._provider = provider
        self._max_tokens = max_tokens
        # The SDK retries 429, 408, 409, 5xx and connection errors with exponential backoff.
        self._client = openai.OpenAI(
            api_key=provider.api_key.get_secret_value(),
            base_url=provider.base_url,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def call_options(self) -> dict[str, str]:
        """Provider options that change the answer, so the cache must key on them too.

        Only options that are switched on appear, so keys made without them stay valid.
        """
        options = self._reasoning_option()
        if self._provider.strict_schema:
            options["strict_schema"] = "true"
        return options

    def _reasoning_option(self) -> dict[str, str]:
        effort = self._provider.reasoning_effort
        return {} if effort is None else {"reasoning_effort": effort}

    def _json_schema(self, request: LLMRequest) -> dict[str, Any]:
        schema: dict[str, Any] = {"name": request.schema_name, "schema": request.response_schema}
        if self._provider.strict_schema:
            schema["strict"] = True
        return schema

    @property
    def _name(self) -> str:
        return self._provider.name

    def complete(self, request: LLMRequest) -> LLMResponse:
        started = time.perf_counter()
        try:
            completion = self._client.chat.completions.create(
                model=self.model,
                messages=_messages(request),
                response_format={"type": "json_schema", "json_schema": self._json_schema(request)},
                temperature=0,
                max_tokens=self._max_tokens,
                **self._reasoning_option(),
            )
        except openai.RateLimitError as error:
            if TOO_LARGE_MARKER in error.message.lower():
                raise RequestTooLargeError(f"{self._name}: {error.message}") from error
            raise RateLimitedError(f"{self._name} rate limit: {error.message}") from error
        except (openai.APITimeoutError, openai.APIConnectionError) as error:
            raise LLMUnavailableError(f"{self._name} unreachable: {error}") from error
        except openai.BadRequestError as error:
            if error.code == SCHEMA_REJECTED_CODE:
                raise SchemaViolationError(
                    f"{self._name} rejected the output: {error.message}",
                    model=self.model,
                    latency_ms=_elapsed_ms(started),
                ) from error
            raise LLMError(f"{self._name} HTTP 400: {error.message}") from error
        except openai.APIStatusError as error:
            raise LLMError(f"{self._name} HTTP {error.status_code}: {error.message}") from error
        return self._to_response(completion, _elapsed_ms(started))

    def _to_response(self, completion: Any, latency_ms: int) -> LLMResponse:
        if not completion.choices or completion.choices[0].message.content is None:
            raise LLMError(f"{self._name} returned no content")
        choice = completion.choices[0]
        if choice.finish_reason == "length":
            raise LLMError(f"{self._name} hit the output token limit")
        # Some providers omit usage; None says "unknown", never a made-up zero.
        usage = None
        if completion.usage is not None:
            usage = Usage(
                input_tokens=completion.usage.prompt_tokens,
                output_tokens=completion.usage.completion_tokens,
            )
        return LLMResponse(
            content=choice.message.content,
            model=completion.model or self.model,
            usage=usage,
            latency_ms=latency_ms,
            cost_usd=None if usage is None else self._cost(usage),
        )

    def _cost(self, usage: Usage) -> Decimal:
        provider = self._provider
        input_cost = usage.input_tokens * provider.input_usd_per_million_tokens
        output_cost = usage.output_tokens * provider.output_usd_per_million_tokens
        return (input_cost + output_cost) / TOKENS_PER_MILLION


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * MS_PER_SECOND)


def _messages(request: LLMRequest) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": request.text}]
    for image in request.images:
        encoded = base64.b64encode(image.data).decode("ascii")
        url = f"data:{image.media_type};base64,{encoded}"
        content.append({"type": "image_url", "image_url": {"url": url}})
    return [
        {"role": "system", "content": request.system},
        {"role": "user", "content": content},
    ]
