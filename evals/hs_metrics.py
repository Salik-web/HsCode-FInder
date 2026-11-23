"""HS suggestion metrics on hs_gold: retrieval recall and rerank accuracy at 4, 6, 8 digits.

Every rate counts all items: a rerank that ended in needs_review or failed is a miss,
never left out. Retrieval-order rates are the baseline the reranker has to beat.
"""

import json
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, Field

from app.schemas.result import LLMCall, StageStatus
from app.schemas.tariff import PCT_CODE_PATTERN
from evals.metrics import ratio
from evals.report import percentile

# heading: 4 digits, subheading: the 6-digit international HS code, national: all 8.
Level = Literal["heading", "subheading", "national"]
LEVELS: tuple[Level, ...] = get_args(Level)
LEVEL_DIGITS: dict[Level, int] = {"heading": 4, "subheading": 6, "national": 8}
HSSplit = Literal["dev", "test"]
TOP_K = 3
# Similarity of the reranker's first choice, for the calibration table.
SIMILARITY_BUCKETS: tuple[tuple[float, float], ...] = (
    (0.0, 0.6),
    (0.6, 0.7),
    (0.7, 0.8),
    (0.8, 1.01),
)


class HSGoldItem(BaseModel):
    id: str
    description: str
    gold_code: str = Field(pattern=PCT_CODE_PATTERN)
    split: HSSplit
    # False until Salik (or a licensed agent) has confirmed the gold code.
    verified: bool


class HSItemOutcome(BaseModel):
    id: str
    gold_code: str
    status: StageStatus
    candidates: list[str]  # retrieval order
    suggestions: list[str]  # rerank order; empty unless status is ok
    top_similarity: float | None  # retrieval similarity of the first suggestion
    calls: list[LLMCall]  # rerank
    query_status: StageStatus
    query_calls: list[LLMCall]


class Rate(BaseModel):
    hits: int
    total: int

    @property
    def value(self) -> float | None:
        return ratio(self.hits, self.total)


class SimilarityBucket(BaseModel):
    low: float
    high: float
    national_top1: Rate


class HSSummary(BaseModel):
    items: int
    statuses: dict[str, int]
    retrieval_recall: dict[Level, Rate]  # gold among the retrieved candidates
    retrieval_top1: dict[Level, Rate]
    retrieval_top3: dict[Level, Rate]
    rerank_top1: dict[Level, Rate]
    rerank_top3: dict[Level, Rate]
    first_try_valid: Rate  # items whose first rerank answer was valid, of items reranked
    retries: int
    query_ok: Rate  # items whose query step gave a valid rewrite
    calibration: list[SimilarityBucket]
    input_tokens: int | None  # None when any call didn't report usage
    output_tokens: int | None
    call_ms_p50: int | None
    call_ms_p95: int | None


def load_hs_gold(path: Path, split: HSSplit | None) -> list[HSGoldItem]:
    items = [
        HSGoldItem.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [item for item in items if split is None or item.split == split]


def same_at(level: Level, code: str, other: str) -> bool:
    digits = LEVEL_DIGITS[level]
    return code.replace(".", "")[:digits] == other.replace(".", "")[:digits]


def hit_at(level: Level, gold: str, ranked: list[str], k: int | None) -> bool:
    """Whether any of the first k codes (all codes when k is None) matches gold at level."""
    considered = ranked if k is None else ranked[:k]
    return any(same_at(level, gold, code) for code in considered)


def _rates(outcomes: list[HSItemOutcome], ranked_by: str, k: int | None) -> dict[Level, Rate]:
    return {
        level: Rate(
            hits=sum(hit_at(level, o.gold_code, getattr(o, ranked_by), k) for o in outcomes),
            total=len(outcomes),
        )
        for level in LEVELS
    }


def _calibration(outcomes: list[HSItemOutcome]) -> list[SimilarityBucket]:
    buckets = []
    for low, high in SIMILARITY_BUCKETS:
        inside = [
            o for o in outcomes if o.top_similarity is not None and low <= o.top_similarity < high
        ]
        hits = sum(hit_at("national", o.gold_code, o.suggestions, 1) for o in inside)
        buckets.append(
            SimilarityBucket(low=low, high=high, national_top1=Rate(hits=hits, total=len(inside)))
        )
    return buckets


def _token_total(calls: list[LLMCall], field: str) -> int | None:
    values = [getattr(call, field) for call in calls]
    return None if any(value is None for value in values) else sum(values)


def summarise_hs(outcomes: list[HSItemOutcome]) -> HSSummary:
    reranked = [o for o in outcomes if o.calls]
    calls = [call for o in outcomes for call in [*o.query_calls, *o.calls]]
    first_valid = sum(not o.calls[0].validation_errors for o in reranked)
    call_times = [call.call_ms for call in calls if call.call_ms is not None]
    statuses: dict[str, int] = {}
    for outcome in outcomes:
        statuses[outcome.status] = statuses.get(outcome.status, 0) + 1
    return HSSummary(
        items=len(outcomes),
        statuses=statuses,
        retrieval_recall=_rates(outcomes, "candidates", None),
        retrieval_top1=_rates(outcomes, "candidates", 1),
        retrieval_top3=_rates(outcomes, "candidates", TOP_K),
        rerank_top1=_rates(outcomes, "suggestions", 1),
        rerank_top3=_rates(outcomes, "suggestions", TOP_K),
        first_try_valid=Rate(hits=first_valid, total=len(reranked)),
        retries=sum(len(o.calls) - 1 for o in reranked),
        query_ok=Rate(hits=sum(o.query_status == "ok" for o in outcomes), total=len(outcomes)),
        calibration=_calibration(outcomes),
        input_tokens=_token_total(calls, "input_tokens"),
        output_tokens=_token_total(calls, "output_tokens"),
        call_ms_p50=percentile(call_times, 50),
        call_ms_p95=percentile(call_times, 95),
    )
