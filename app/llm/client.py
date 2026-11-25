"""The provider-neutral interface every LLM call goes through."""

import re
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

MS_PER_SECOND = 1000
# Provider errors name the account (Groq's rate limits quote the organization) and may echo
# part of a key. They are stored with runs and written into committed eval reports.
ACCOUNT_ID = re.compile(
    r"\b(?P<kind>org|proj|project|user|acct|account|req)[-_](?=[A-Za-z0-9]*\d)[A-Za-z0-9]{8,}\b"
    r"|\b(?P<key>gsk_|sk-|AIza)[A-Za-z0-9_*-]{4,}"
    r"|(?P<number>\bprojects/|\bproject_number:\s*)\d{5,}"
)


def strip_account_ids(text: str) -> str:
    """The text with account, project, request and key identifiers replaced by their kind."""
    return ACCOUNT_ID.sub(_redacted, text)


def _redacted(match: re.Match[str]) -> str:
    if match["kind"]:
        return f"{match['kind']}_[redacted]"
    return f"{match['key'] or match['number']}[redacted]"


class LLMError(RuntimeError):
    """An LLM call failed after the provider's own retries.

    The exception's own text is the technical detail, kept with the run, without account
    identifiers. `plain` is what a reviewer reads; it never quotes the provider.
    """

    plain = (
        "The AI service couldn't process this document. Your file is kept: try again, and "
        "report it if it keeps happening."
    )

    def __init__(self, message: str) -> None:
        super().__init__(strip_account_ids(message))


class RateLimitedError(LLMError):
    """The provider refused the call for rate or quota reasons; try later or elsewhere."""

    plain = "The AI service's usage limit has been reached. Wait a while, then try again."


class LLMUnavailableError(LLMError):
    """The provider timed out or couldn't be reached."""

    plain = "The AI service didn't respond. Wait a few minutes, then try again."


class ProviderAccessError(LLMError):
    """The provider refused the API key, or the key may not use this model."""

    plain = "The AI service refused this app's API key. Check the key, then try again."


class NotCachedError(LLMError):
    """An offline run needed an answer that isn't in the cache."""

    plain = "There is no saved answer for this document, and the app is running offline."


class RequestTooLargeError(LLMError):
    """The request can never fit the provider's limits; waiting won't help."""

    plain = "This document is too large for the AI service. Try fewer pages or a smaller file."


class TooManyImagesError(LLMError):
    """The request has more page images than the model accepts; it was never sent."""

    plain = "This document has more pages than the AI service accepts. Try fewer pages."


class OutputLimitError(LLMError):
    """The answer was cut off at the model's output limit."""

    plain = (
        "This invoice has too many lines for the AI service to return in one answer. Try "
        "splitting it into smaller files."
    )


class SchemaViolationError(LLMError):
    """The provider rejected the model's output for not matching the JSON schema.

    Carries what a successful call would, so extraction can log the attempt and retry
    with the error, exactly as when our own validation rejects an answer.
    """

    def __init__(
        self,
        message: str,
        model: str,
        latency_ms: int,
        call_ms: int | None = None,
        cached: bool = False,
    ) -> None:
        super().__init__(message)
        self.model = model
        self.latency_ms = latency_ms
        self.call_ms = call_ms
        # Set by the throttle on the way out, like LLMResponse.throttle_ms.
        self.throttle_ms: int | None = None
        self.cached = cached


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
    # From sending the request to the answer, including the SDK's retries and backoff.
    latency_ms: int
    # Model call time: the final HTTP attempt only, request sent to response read. The rest
    # of latency_ms is backoff. None for answers cached before it was recorded.
    call_ms: int | None = None
    # Client-side wait before the call (app/llm/throttle.py); not part of latency_ms.
    throttle_ms: int | None = None
    cost_usd: Decimal | None
    # True when served from the disk cache; latency and cost are the original call's.
    cached: bool = False


class LLMClient(Protocol):
    @property
    def model(self) -> str: ...

    def complete(self, request: LLMRequest) -> LLMResponse: ...
