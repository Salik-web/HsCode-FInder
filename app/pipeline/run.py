"""The whole pipeline as pure functions: the API, worker, CLI and evals all call these.

Preprocessing and the LLM stages are separate entry points, because PDFium must stay on
one thread while LLM calls may run concurrently (see evals/run.py).
"""

import hashlib
from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.llm.client import LLMClient
from app.pipeline.confidence import assess_fields
from app.pipeline.extract import ExtractionOutcome, extract_invoice
from app.pipeline.preprocess import (
    InvalidDocumentError,
    PreparedDocument,
    first_pages,
    prepare_document,
)
from app.pipeline.prompts import Prompt, load_prompt
from app.pipeline.validate import HSCodeTable, verify_invoice
from app.schemas.result import PipelineResult, StageResult

NOT_RUN = StageResult(status="not_run", error=None, retryable=False)


class PipelineConfig(BaseModel):
    prompt_version: str
    # The day invoice dates are judged against: today in the API, a fixed day in evals,
    # so a report doesn't change with the day it is run.
    as_of: date
    doc_type: Literal["invoice"] = "invoice"
    # The tariff's codes for the HS-code check; None skips it (no tariff loaded).
    hs_codes: HSCodeTable | None = None
    # The most page images the model accepts (Settings.extraction_max_images); longer
    # documents are read up to it, with a warning. None: no limit.
    max_images: int | None = None


class Preprocessed(BaseModel):
    document_sha256: str
    stage: StageResult
    document: PreparedDocument | None


def preprocess_stage(file_bytes: bytes) -> Preprocessed:
    """Run preprocessing and turn a bad file into a failed stage, not an exception."""
    sha256 = hashlib.sha256(file_bytes).hexdigest()
    try:
        document = prepare_document(file_bytes)
    except InvalidDocumentError as error:
        failed = StageResult(status="failed", error=str(error), retryable=False)
        return Preprocessed(document_sha256=sha256, stage=failed, document=None)
    ok = StageResult(status="ok", error=None, retryable=False)
    return Preprocessed(document_sha256=sha256, stage=ok, document=document)


def complete_pipeline(
    preprocessed: Preprocessed, config: PipelineConfig, client: LLMClient
) -> PipelineResult:
    """Run every stage after preprocessing. A failure keeps earlier stages' results."""
    prompt = load_prompt(config.doc_type, config.prompt_version)
    result = PipelineResult(
        document_sha256=preprocessed.document_sha256,
        prompt_version=prompt.version,
        model=client.model,
        page_count=None,
        preprocess=preprocessed.stage,
        extract=NOT_RUN,
        invoice=None,
        llm_calls=[],
        rules_applied=[],
        field_checks=[],
    )
    if preprocessed.document is None:
        return result
    document, outcome = _extract_pages_that_fit(
        first_pages(preprocessed.document, config.max_images), prompt, client
    )
    total, read = len(preprocessed.document.pages), len(document.pages)
    result = result.model_copy(
        update={
            "page_count": total,
            "pages_read": read,
            "warnings": [] if read == total else [unread_pages_warning(read, total)],
            "extract": outcome.stage,
            "llm_calls": outcome.calls,
        }
    )
    if outcome.invoice is None:
        return result
    text_layer = document.text_layer
    rules = verify_invoice(
        outcome.invoice, config.as_of, text_layer=text_layer, hs_codes=config.hs_codes
    )
    applied = [*outcome.changes, *rules.applied]
    assessments = assess_fields(rules.invoice, text_layer, outcome.calls, applied, rules.checks)
    return result.model_copy(
        update={
            "invoice": rules.invoice,
            "rules_applied": applied,
            "field_checks": rules.checks,
            "assessments": assessments,
        }
    )


def _extract_pages_that_fit(
    document: PreparedDocument, prompt: Prompt, client: LLMClient
) -> tuple[PreparedDocument, ExtractionOutcome]:
    """Extract, dropping the last page while the provider says the request is too large.

    The image limit alone isn't enough: on Groq's free tier three pages pass it but exceed
    the input tokens allowed in a minute (7,000), which depends on the pages' content.
    A refused request costs no tokens. One page that is too large stays a failure.
    """
    outcome = extract_invoice(document, prompt, client)
    while outcome.too_large and len(document.pages) > 1:
        document = first_pages(document, len(document.pages) - 1)
        refused = outcome
        outcome = extract_invoice(document, prompt, client)
        outcome = outcome.model_copy(update={"calls": [*refused.calls, *outcome.calls]})
    return document, outcome


def unread_pages_warning(read: int, total: int) -> str:
    return (
        f"Only the first {read} of {total} pages were read. Check the other pages for "
        "invoice lines, totals or charges."
    )


def run_pipeline(file_bytes: bytes, config: PipelineConfig, client: LLMClient) -> PipelineResult:
    return complete_pipeline(preprocess_stage(file_bytes), config, client)
