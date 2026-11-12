"""LLM extraction into RawInvoice: schema-validated, one retry with the errors, else review.

Design rule 1: output that fails validation twice is never returned; the stage reports
`needs_review` with no invoice. A provider that enforces the schema itself and rejects
the output counts as a failed validation too, with the same single retry.
"""

import json
import logging
from decimal import Decimal
from functools import cache
from typing import Any

from pydantic import BaseModel, ValidationError

from app.llm.client import (
    LLMClient,
    LLMError,
    LLMRequest,
    LLMResponse,
    LLMUnavailableError,
    NotCachedError,
    RateLimitedError,
    SchemaViolationError,
)
from app.pipeline.numbers import NumberReport, normalise_numbers, with_placeholders
from app.pipeline.preprocess import PreparedDocument
from app.pipeline.prompts import OutputFormat, Prompt
from app.pipeline.repair import NO_REPAIRS, KeyRepairs, describe_allowed_keys, repair_keys
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
RETRYABLE_ERRORS = (RateLimitedError, LLMUnavailableError, NotCachedError)


REPEATED_ANSWER = "(whole answer): the retry repeated the previous answer unchanged"
NO_NUMBER_CHANGES = NumberReport(normalised=[], ambiguous=[])


class OutputRejectedError(ValueError):
    """The model's answer isn't usable: not JSON, or not valid against the schema."""

    def __init__(self, errors: list[str], repairs: KeyRepairs, numbers: NumberReport) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors
        self.repairs = repairs
        self.numbers = numbers


class AmbiguousNumbersError(ValueError):
    """Valid output whose number formats can't be read with certainty: a person decides."""

    def __init__(self, repairs: KeyRepairs, numbers: NumberReport) -> None:
        super().__init__(f"ambiguous number format at {', '.join(numbers.ambiguous)}")
        self.repairs = repairs
        self.numbers = numbers


class ParsedOutput(BaseModel):
    invoice: RawInvoice
    repairs: KeyRepairs
    numbers: NumberReport


class ExtractionOutcome(BaseModel):
    stage: StageResult
    invoice: RawInvoice | None
    calls: list[LLMCall]


@cache
def response_schema(output_format: OutputFormat) -> dict[str, Any]:
    """Serialization mode makes every Decimal a string, so amounts come back exact."""
    return OUTPUT_MODELS[output_format].model_json_schema(mode="serialization")


@cache
def allowed_keys(output_format: OutputFormat) -> str:
    return describe_allowed_keys(OUTPUT_MODELS[output_format])


def parse_output(content: str, output_format: OutputFormat) -> ParsedOutput:
    """Parse, repair key names, normalise number strings, then validate.

    Raises OutputRejectedError when the answer is unusable (worth one retry), and
    AmbiguousNumbersError when it's valid but a number's format can't be read with
    certainty (a person decides). JSON numbers parse as Decimal, never float.
    """
    try:
        data = json.loads(content, parse_float=Decimal)
    except json.JSONDecodeError as error:
        message = f"(whole answer): not valid JSON: {error.msg}"
        raise OutputRejectedError([message], NO_REPAIRS, NO_NUMBER_CHANGES) from error
    model = OUTPUT_MODELS[output_format]
    repaired, repairs = repair_keys(data, model)
    normalised, numbers = normalise_numbers(repaired, model)
    # Validate everything except the ambiguous numbers: other errors are worth a retry,
    # but an ambiguous format alone isn't something the model can settle.
    checkable = (
        with_placeholders(normalised, numbers.ambiguous) if numbers.ambiguous else normalised
    )
    try:
        parsed = model.model_validate(checkable)
    except ValidationError as error:
        errors = format_validation_errors(error)
        raise OutputRejectedError(errors, repairs, numbers) from error
    if numbers.ambiguous:
        raise AmbiguousNumbersError(repairs, numbers)
    invoice = parsed.to_raw() if isinstance(parsed, CompactInvoice) else parsed
    return ParsedOutput(invoice=invoice, repairs=repairs, numbers=numbers)


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
    previous_content: str | None = None
    keys = allowed_keys(prompt.output_format)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        text = (
            prompt.user_message(document.text_layer)
            if attempt == 1
            else prompt.retry_message(document.text_layer, errors, keys)
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
        if previous_content is not None and response.content.strip() == previous_content:
            # The retry ignored the errors; asking again would only repeat it.
            calls.append(
                _call_record(attempt, response, [REPEATED_ANSWER], NO_REPAIRS, NO_NUMBER_CHANGES)
            )
            review = StageResult(status="needs_review", error=REPEATED_ANSWER, retryable=False)
            return ExtractionOutcome(stage=review, invoice=None, calls=calls)
        previous_content = response.content.strip()
        try:
            parsed = parse_output(response.content, prompt.output_format)
        except OutputRejectedError as error:
            errors = error.errors
            calls.append(_call_record(attempt, response, errors, error.repairs, error.numbers))
            continue
        except AmbiguousNumbersError as error:
            # Asking the model again can't settle how the document writes its numbers.
            calls.append(_call_record(attempt, response, [], error.repairs, error.numbers))
            review = StageResult(status="needs_review", error=str(error), retryable=False)
            return ExtractionOutcome(stage=review, invoice=None, calls=calls)
        calls.append(_call_record(attempt, response, [], parsed.repairs, parsed.numbers))
        ok = StageResult(status="ok", error=None, retryable=False)
        return ExtractionOutcome(stage=ok, invoice=parsed.invoice, calls=calls)
    review = StageResult(
        status="needs_review",
        error=f"output failed validation {MAX_ATTEMPTS} times",
        retryable=False,
    )
    return ExtractionOutcome(stage=review, invoice=None, calls=calls)


def _request(document: PreparedDocument, prompt: Prompt, text: str, attempt: int) -> LLMRequest:
    return LLMRequest(
        system=prompt.system_message(allowed_keys(prompt.output_format)),
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


def _call_record(
    attempt: int,
    response: LLMResponse,
    errors: list[str],
    repairs: KeyRepairs,
    numbers: NumberReport,
) -> LLMCall:
    return LLMCall(
        attempt=attempt,
        model=response.model,
        input_tokens=None if response.usage is None else response.usage.input_tokens,
        output_tokens=None if response.usage is None else response.usage.output_tokens,
        latency_ms=response.latency_ms,
        cost_usd=response.cost_usd,
        cached=response.cached,
        validation_errors=errors,
        keys_repaired=repairs.repaired,
        keys_ignored=repairs.ignored,
        numbers_normalised=numbers.normalised,
        numbers_ambiguous=numbers.ambiguous,
    )
