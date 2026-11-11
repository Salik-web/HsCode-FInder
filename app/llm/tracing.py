"""Langfuse tracing for any LLMClient.

Traces hold metadata only: sizes, token counts, latency, cost, model and prompt version.
No image, document text or model output is sent, so a real document can't leak this way.
"""

from typing import Any, Protocol

from app.llm.client import LLMClient, LLMError, LLMRequest, LLMResponse

GENERATION_NAME = "extract"


class Observation(Protocol):
    def update(self, **fields: Any) -> Any: ...

    def end(self) -> Any: ...


class Tracer(Protocol):
    """The part of the Langfuse client this module uses."""

    def start_observation(self, **fields: Any) -> Observation: ...


def request_summary(request: LLMRequest) -> dict[str, Any]:
    return {
        "document_sha256": request.document_sha256,
        "attempt": request.attempt,
        "image_count": len(request.images),
        "image_bytes": sum(len(image.data) for image in request.images),
        "text_chars": len(request.text),
        "system_chars": len(request.system),
    }


class TracingClient:
    def __init__(self, inner: LLMClient, tracer: Tracer) -> None:
        self._inner = inner
        self._tracer = tracer

    @property
    def model(self) -> str:
        return self._inner.model

    def complete(self, request: LLMRequest) -> LLMResponse:
        # Started before the call, so the trace shows the real duration.
        observation = self._tracer.start_observation(
            name=GENERATION_NAME,
            as_type="generation",
            model=self.model,
            version=request.prompt_version,
            input=request_summary(request),
        )
        try:
            response = self._inner.complete(request)
        except LLMError as error:
            observation.update(level="ERROR", status_message=type(error).__name__)
            observation.end()
            raise
        observation.update(
            model=response.model,
            output={"output_chars": len(response.content)},
            usage_details=_usage_details(response),
            cost_details=None if response.cost_usd is None else {"total": float(response.cost_usd)},
            metadata={"latency_ms": response.latency_ms},
        )
        observation.end()
        return response


def _usage_details(response: LLMResponse) -> dict[str, int] | None:
    if response.usage is None:
        return None
    return {"input": response.usage.input_tokens, "output": response.usage.output_tokens}
