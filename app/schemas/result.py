"""What one pipeline run returns: per-stage outcomes plus everything needed to reproduce it."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.schemas.constants import SCHEMA_VERSION, SchemaVersion
from app.schemas.invoice import FieldStatus
from app.schemas.raw import RawInvoice

# ok: done. needs_review: ran, but output can't be trusted without a person.
# failed: errored; `retryable` says whether running again later may succeed.
# not_run: an earlier stage failed, so this one never started.
StageStatus = Literal["ok", "needs_review", "failed", "not_run"]


class StageResult(BaseModel):
    status: StageStatus
    error: str | None  # technical detail
    retryable: bool
    # A provider error in plain language, for the reviewer. None for anything else.
    message: str | None = None


class FieldCheck(BaseModel):
    """A value a deterministic rule rejected. The value is kept; a person decides."""

    path: str  # e.g. "line_items[3].unit"
    status: Literal["failed_check"]
    rule: str  # e.g. "unit_list"
    detail: str


class FieldAssessment(BaseModel):
    """How far one extracted value can be trusted, and why (app/pipeline/confidence.py)."""

    path: str  # e.g. "total_amount", "buyer.name", "line_items[2].amount"
    confidence: float  # 0-1, from documented deterministic signals, never the model's own
    status: FieldStatus
    reasons: list[str]  # every adjustment, in order, so the score can be explained


class LLMCall(BaseModel):
    """One model call. Metadata only: never the document or the model's output text."""

    attempt: int
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int  # model call time plus SDK retry backoff
    # Model call time (final HTTP attempt) and client-side throttle wait. None for calls
    # recorded before 2026-11-16, when only latency_ms existed.
    call_ms: int | None = None
    throttle_ms: int | None = None
    cost_usd: Decimal | None
    cached: bool
    validation_errors: list[str]
    # Key names fixed or dropped before validation (app/pipeline/repair.py). Reports
    # written before repair existed have none.
    keys_repaired: list[str] = []
    keys_ignored: list[str] = []
    # Number strings rewritten as plain decimals, and those left because their format was
    # ambiguous (app/pipeline/numbers.py). Paths only, never values.
    numbers_normalised: list[str] = []
    numbers_ambiguous: list[str] = []


class PipelineResult(BaseModel):
    document_sha256: str
    schema_version: SchemaVersion = SCHEMA_VERSION
    prompt_version: str
    model: str  # the model configured; each LLMCall records the one that answered
    page_count: int | None  # pages in the file
    # Pages sent to the model: fewer than page_count when the model's image limit cut the
    # document. None when nothing was read, and on runs before 2026-11-25.
    pages_read: int | None = None
    # Plain-language cautions about the whole document; any warning sends it to review.
    warnings: list[str] = []
    preprocess: StageResult
    extract: StageResult
    invoice: RawInvoice | None  # set only when extract is ok: validated output or nothing
    llm_calls: list[LLMCall]
    # Changes deterministic rules made after validation, and values the rules and the
    # guardrails rejected (app/pipeline/rules.py, app/pipeline/validate.py).
    rules_applied: list[str]
    field_checks: list[FieldCheck]
    # Confidence and review status of every field (app/pipeline/confidence.py); empty when
    # there is no validated invoice.
    assessments: list[FieldAssessment] = []
