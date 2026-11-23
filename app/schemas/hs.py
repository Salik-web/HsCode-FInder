"""HS (PCT) code suggestion: retrieved candidates, the reranker's answer, the stage result.

Suggestions are always suggestions: a licensed person makes the final classification.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.tariff import PCT_CODE_PATTERN

MAX_SUGGESTIONS = 3
MAX_REASON_CHARS = 300


class HSCandidate(BaseModel):
    """A tariff row found by retrieval for one line description."""

    code: str = Field(pattern=PCT_CODE_PATTERN)
    path: str
    rank: int = Field(ge=1)  # 1 = best
    # Cosine similarity of query and tariff text embeddings; None when only the
    # full-text search found this code.
    similarity: float | None


class HSSuggestion(BaseModel):
    code: str = Field(pattern=PCT_CODE_PATTERN)
    reason: str
    # Where retrieval put this code. A deterministic signal, kept so confidence can be
    # calibrated on hs_gold; the model's own confidence is never used.
    retrieval_rank: int = Field(ge=1)
    similarity: float | None


class RerankChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    reason: str = Field(max_length=MAX_REASON_CHARS)


class RerankAnswer(BaseModel):
    """What the reranker must return: 1-3 candidate codes, best first."""

    model_config = ConfigDict(extra="forbid")

    suggestions: list[RerankChoice] = Field(min_length=1, max_length=MAX_SUGGESTIONS)
