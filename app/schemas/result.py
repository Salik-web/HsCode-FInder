"""What one pipeline run returns: per-stage outcomes plus everything needed to reproduce it."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.schemas.constants import SCHEMA_VERSION, SchemaVersion
from app.schemas.raw import RawInvoice

# ok: done. needs_review: ran, but output can't be trusted without a person.
# failed: errored; `retryable` says whether running again later may succeed.
# not_run: an earlier stage failed, so this one never started.
StageStatus = Literal["ok", "needs_review", "failed", "not_run"]


class StageResult(BaseModel):
    status: StageStatus
    error: str | None
    retryable: bool


class LLMCall(BaseModel):
    """One model call. Metadata only: never the document or the model's output text."""

    attempt: int
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    cost_usd: Decimal | None
    cached: bool
    validation_errors: list[str]


class PipelineResult(BaseModel):
    document_sha256: str
    schema_version: SchemaVersion = SCHEMA_VERSION
    prompt_version: str
    model: str  # the model configured; each LLMCall records the one that answered
    page_count: int | None
    preprocess: StageResult
    extract: StageResult
    invoice: RawInvoice | None  # set only when extract is ok: validated output or nothing
    llm_calls: list[LLMCall]
