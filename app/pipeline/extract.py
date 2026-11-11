"""LLM extraction into RawInvoice: schema-validated, one retry with the errors, else review.

Design rule 1: output that fails validation twice is never returned; the stage reports
`needs_review` with no invoice. A provider that enforces the schema itself and rejects
the output counts as a failed validation too, with the same single retry.
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
    SchemaViolationError,
)
from app.pipeline.preprocess import PreparedDocument
from app.pipeline.prompts import OutputFormat, Prompt
from app.schemas.compact import CompactInvoice
from app.schemas.raw import RawInvoice
from app.schemas.result import LLMCall, StageResult

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2  # the first try plus one retry with the validation errors
MAX_ERRORS_IN_RETRY = 20  # keeps a badly broken answer from bloating the retry prompt
MAX_PROVIDER_ERROR_CHARS = 500
# What the model fills for each output format; both convert to RawInvoice.
OUTPUT_MODELS: dict[OutputFormat, type[RawInvoice]] = {
    "raw": RawInvoice,
    "compact": CompactInvoice,
}
# Waiting may fix these; any other LLM error would fail the same way again.
RETRYABLE_ERRORS = (RateLimitedError, LLMUnavailableError)


class ExtractionOutcome(BaseModel):
    stage: StageResult
    invoice: RawInvoice | None
    calls: list[LLMCall]


@cache
def response_schema(output_format: OutputFormat) -> dict[str, Any]:
    """Serialization mode makes every Decimal a string, so amounts come back exact."""
    return OUTPUT_MODELS[output_format].model_json_schema(mode="serialization")


def parse_output(content: str, output_format: OutputFormat) -> RawInvoice:
    """Validate the model's JSON in its output format and return it as a RawInvoice."""
    parsed = OUTPUT_MODELS[output_format].model_validate_json(content)
    return parsed.to_raw() if isinstance(parsed, CompactInvoice) else parsed


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
        except SchemaViolationError as error:
            errors = [f"(whole answer): {str(error)[:MAX_PROVIDER_ERROR_CHARS]}"]
            calls.append(_rejected_call_record(attempt, error, errors))
            continue
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
            invoice = parse_output(response.content, prompt.output_format)
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
        response_schema=response_schema(prompt.output_format),
        schema_name=OUTPUT_MODELS[prompt.output_format].__name__,
        document_sha256=document.document_sha256,
        prompt_version=prompt.version,
        attempt=attempt,
    )


def _rejected_call_record(attempt: int, error: SchemaViolationError, errors: list[str]) -> LLMCall:
    """The provider reports no usage for an output it rejected, so tokens are unknown."""
    return LLMCall(
        attempt=attempt,
        model=error.model,
        input_tokens=None,
        output_tokens=None,
        latency_ms=error.latency_ms,
        cost_usd=None,
        cached=False,
        validation_errors=errors,
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
