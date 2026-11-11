"""The provider-neutral interface every LLM call goes through."""

from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


class LLMError(RuntimeError):
    """An LLM call failed after the provider's own retries."""


class RateLimitedError(LLMError):
    """The provider refused the call for rate or quota reasons; try later or elsewhere."""


class LLMUnavailableError(LLMError):
    """The provider timed out or couldn't be reached."""


class RequestTooLargeError(LLMError):
    """The request can never fit the provider's limits; waiting won't help."""


class SchemaViolationError(LLMError):
    """The provider rejected the model's output for not matching the JSON schema.

    Carries what a successful call would, so extraction can log the attempt and retry
    with the error, exactly as when our own validation rejects an answer.
    """

    def __init__(self, message: str, model: str, latency_ms: int) -> None:
        super().__init__(message)
        self.model = model
        self.latency_ms = latency_ms


class ImageInput(BaseModel):
    # Page images are binary, so JSON (cache digests) carries them as base64.
    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")

    media_type: str  # e.g. "image/png"
    data: bytes


class LLMRequest(BaseModel):
    system: str
    text: str
    images: list[ImageInput]
    response_schema: dict[str, Any]
    schema_name: str
    # Identify the call for caching and tracing. Never document content.
    document_sha256: str
    prompt_version: str
    attempt: int


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int


class LLMResponse(BaseModel):
    content: str
    model: str
    usage: Usage | None  # None when the provider didn't report it
    latency_ms: int
    cost_usd: Decimal | None
    # True when served from the disk cache; latency and cost are the original call's.
    cached: bool = False


class LLMClient(Protocol):
    @property
    def model(self) -> str: ...

    def complete(self, request: LLMRequest) -> LLMResponse: ...
