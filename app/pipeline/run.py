"""The whole pipeline as pure functions: the API, worker, CLI and evals all call these.

Preprocessing and the LLM stages are separate entry points, because PDFium must stay on
one thread while LLM calls may run concurrently (see evals/run.py).
"""

import hashlib
from typing import Literal

from pydantic import BaseModel

from app.llm.client import LLMClient
from app.pipeline.extract import extract_invoice
from app.pipeline.preprocess import InvalidDocumentError, PreparedDocument, prepare_document
from app.pipeline.prompts import load_prompt
from app.pipeline.rules import apply_rules
from app.schemas.result import PipelineResult, StageResult

NOT_RUN = StageResult(status="not_run", error=None, retryable=False)


class PipelineConfig(BaseModel):
    prompt_version: str
    doc_type: Literal["invoice"] = "invoice"


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
    outcome = extract_invoice(preprocessed.document, prompt, client)
    result = result.model_copy(
        update={
            "page_count": len(preprocessed.document.pages),
            "extract": outcome.stage,
            "llm_calls": outcome.calls,
        }
    )
    if outcome.invoice is None:
        return result
    rules = apply_rules(outcome.invoice)
    return result.model_copy(
        update={
            "invoice": rules.invoice,
            "rules_applied": rules.applied,
            "field_checks": rules.checks,
        }
    )


def run_pipeline(file_bytes: bytes, config: PipelineConfig, client: LLMClient) -> PipelineResult:
    return complete_pipeline(preprocess_stage(file_bytes), config, client)
