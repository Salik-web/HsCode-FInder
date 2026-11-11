"""LLM extraction into RawInvoice: schema-validated, one retry with the errors, else review.

Design rule 1: output that fails validation twice is never returned; the stage reports
`needs_review` with no invoice.
"""

import logging
from functools import cache
from typing import Any

from pydantic import BaseModel, ValidationError

from app.llm.client import (
    LLMClient,
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
    RateLimitedError,
)
from app.pipeline.preprocess import PreparedDocument
from app.pipeline.prompts import Prompt
from app.schemas.raw import RawInvoice
from app.schemas.result import LLMCall, StageResult

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2  # the first try plus one retry with the validation errors
MAX_ERRORS_IN_RETRY = 20  # keeps a badly broken answer from bloating the retry prompt
SCHEMA_NAME = "RawInvoice"
# Waiting may fix these; any other LLM error would fail the same way again.
RETRYABLE_ERRORS = (RateLimitedError, LLMUnavailableError)


class ExtractionOutcome(BaseModel):
    stage: StageResult
    invoice: RawInvoice | None
    calls: list[LLMCall]


@cache
def response_schema() -> dict[str, Any]:
    """Serialization mode makes every Decimal a string, so amounts come back exact."""
    return RawInvoice.model_json_schema(mode="serialization")


def format_validation_errors(error: ValidationError) -> list[str]:
    """Field paths and messages only; the rejected values stay out of logs and prompts."""
    messages = []
    for detail in error.errors(include_url=False, include_input=False)[:MAX_ERRORS_IN_RETRY]:
        location = ".".join(str(part) for part in detail["loc"]) or "(whole answer)"
        messages.append(f"{location}: {detail['msg']}")
    return messages


def extract_invoice(
    document: PreparedDocument, prompt: Prompt, client: LLMClient
) -> ExtractionOutcome:
    calls: list[LLMCall] = []
    errors: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        text = (
            prompt.user_message(document.text_layer)
            if attempt == 1
            else prompt.retry_message(document.text_layer, errors)
        )
        try:
            response = client.complete(_request(document, prompt, text, attempt))
        except LLMError as error:
            logger.warning(
                "LLM call failed",
                extra={"document_sha256": document.document_sha256, "attempt": attempt},
            )
            stage = StageResult(
                status="failed",
                error=f"{type(error).__name__}: {error}",
                retryable=isinstance(error, RETRYABLE_ERRORS),
            )
            return ExtractionOutcome(stage=stage, invoice=None, calls=calls)
        try:
            invoice = RawInvoice.model_validate_json(response.content)
        except ValidationError as error:
            errors = format_validation_errors(error)
            calls.append(_call_record(attempt, response, errors))
            continue
        calls.append(_call_record(attempt, response, []))
        ok = StageResult(status="ok", error=None, retryable=False)
        return ExtractionOutcome(stage=ok, invoice=invoice, calls=calls)
    review = StageResult(
        status="needs_review",
        error=f"output failed validation {MAX_ATTEMPTS} times",
        retryable=False,
    )
    return ExtractionOutcome(stage=review, invoice=None, calls=calls)


def _request(document: PreparedDocument, prompt: Prompt, text: str, attempt: int) -> LLMRequest:
    return LLMRequest(
        system=prompt.system,
        text=text,
        images=document.pages,
        response_schema=response_schema(),
        schema_name=SCHEMA_NAME,
        document_sha256=document.document_sha256,
        prompt_version=prompt.version,
        attempt=attempt,
    )


def _call_record(attempt: int, response: LLMResponse, errors: list[str]) -> LLMCall:
    return LLMCall(
        attempt=attempt,
        model=response.model,
        input_tokens=None if response.usage is None else response.usage.input_tokens,
        output_tokens=None if response.usage is None else response.usage.output_tokens,
        latency_ms=response.latency_ms,
        cost_usd=response.cost_usd,
        cached=response.cached,
        validation_errors=errors,
    )
