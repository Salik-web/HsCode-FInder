"""HS code suggestion for one line: understand the query, retrieve PCT rows, rerank.

Design rule 3: every suggested code must be one of the retrieved candidates; any other
code is a hallucination and the answer is rejected. Every LLM step validates its answer
with Pydantic and gets one retry with the errors; after that the stage is
`needs_review` with nothing from it. Suggestions are always suggestions: a licensed
person makes the final classification.
"""

import hashlib
import json
import logging
from collections.abc import Callable
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
from app.pipeline.extract import format_validation_errors
from app.pipeline.printed_hs import (
    PrintedCodeMismatch,
    printed_code_mismatch,
    split_printed_code,
)
from app.pipeline.prompts import TaskPrompt
from app.schemas.hs import HSCandidate, HSSuggestion, RerankAnswer
from app.schemas.result import LLMCall, StageResult

logger = logging.getLogger(__name__)

RETRIEVAL_LIMIT = 10  # candidates the reranker sees
# Each ranking fed into the fusion is this deep (Salik's lever study, 2026-11-23).
RANKING_DEPTH = 50
MAX_ATTEMPTS = 2  # the first try plus one retry with the errors
# Long national lines (chapter 87 parts lists run to 11,000 characters) would swamp the
# prompt; each level of a candidate's path is cut to this length.
MAX_PATH_PART_CHARS = 160
MAX_QUERY_CHARS = 600
MAX_HEADINGS = 3
PATH_SEPARATOR = " > "
ELLIPSIS = "…"
# Reciprocal rank fusion: score = sum of 1 / (k + rank). 60 is the usual constant; it
# keeps one list's top rank from outweighing agreement between lists.
RRF_K = 60
RETRYABLE_ERRORS = (RateLimitedError, LLMUnavailableError, NotCachedError)


class Embedder(Protocol):
    def embed_query(self, text: str) -> list[float]: ...


class TariffIndex(Protocol):
    def search(self, query_vector: list[float], limit: int) -> list[HSCandidate]: ...

    def search_text(self, query: str, limit: int) -> list[HSCandidate]: ...


class AnswerRejectedError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class QueryAnswer(BaseModel):
    """The query step's answer: the goods in tariff language, and likely headings."""

    model_config = ConfigDict(extra="forbid")

    tariff_description: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    headings: list[str] = Field(min_length=1, max_length=MAX_HEADINGS)


class QueryResult(BaseModel):
    stage: StageResult
    answer: QueryAnswer | None  # set only when stage is ok
    calls: list[LLMCall]


class HSResult(BaseModel):
    stage: StageResult
    # Set only when stage is ok: validated codes from `candidates`, best first.
    suggestions: list[HSSuggestion]
    candidates: list[HSCandidate]
    calls: list[LLMCall]


class HSLineResult(BaseModel):
    """Both LLM steps for one line; `hs` holds the suggestions."""

    query: QueryResult
    hs: HSResult
    # Set when the line prints an HS code our top suggestion disagrees with.
    mismatch: PrintedCodeMismatch | None = None


class _Asked[T](BaseModel):
    stage: StageResult
    parsed: T | None
    calls: list[LLMCall]


def retrieve_candidates(
    description: str,
    rewrite: str | None,
    embedder: Embedder,
    index: TariffIndex,
    limit: int = RETRIEVAL_LIMIT,
) -> list[HSCandidate]:
    """Vector and full-text rankings of the description and, when the query step gave
    one, of its tariff-language rewrite, fused by reciprocal rank."""
    queries = [description] if rewrite is None else [description, rewrite]
    rankings: list[list[HSCandidate]] = []
    for query in queries:
        rankings.append(index.search(embedder.embed_query(query), RANKING_DEPTH))
        rankings.append(index.search_text(query, RANKING_DEPTH))
    return fuse_rankings([ranking for ranking in rankings if ranking], limit)


def classify_line(
    description: str,
    embedder: Embedder,
    index: TariffIndex,
    query_prompt: TaskPrompt,
    rerank_prompt: TaskPrompt,
    client: LLMClient,
    printed_code: str | None = None,
) -> HSLineResult:
    """Query understanding, fused retrieval, rerank, then the printed code's comparison.

    `printed_code` is the line's hs_code_on_doc. The models never see it, or a code left in
    the description: they would repeat it. It is only compared with their answer.

    A query step that ends in needs_review (or a non-retryable failure) doesn't stop the
    line: retrieval uses the description alone, and the query stage says why. A
    retryable failure (rate limit) stops it, since the rerank call would fail the same way.
    """
    goods = split_printed_code(description, printed_code).description
    query = understand_query(goods, query_prompt, client)
    if query.stage.status == "failed" and query.stage.retryable:
        hs = HSResult(stage=query.stage, suggestions=[], candidates=[], calls=[])
        return HSLineResult(query=query, hs=hs)
    rewrite = None if query.answer is None else query.answer.tariff_description
    candidates = retrieve_candidates(goods, rewrite, embedder, index)
    hs = suggest_hs_codes(goods, candidates, rerank_prompt, client)
    return HSLineResult(
        query=query, hs=hs, mismatch=printed_code_mismatch(printed_code, hs.suggestions)
    )


def fuse_rankings(rankings: list[list[HSCandidate]], limit: int) -> list[HSCandidate]:
    """Reciprocal rank fusion of several rankings; ranks are renumbered from 1.

    A code keeps the highest vector similarity any ranking gave it (None if only full-text
    search found it). Ties break by code, so the order is reproducible.
    """
    scores: dict[str, float] = {}
    best: dict[str, HSCandidate] = {}
    for ranking in rankings:
        for candidate in ranking:
            scores[candidate.code] = scores.get(candidate.code, 0.0) + 1 / (RRF_K + candidate.rank)
            kept = best.get(candidate.code)
            if kept is None or _more_similar(candidate, kept):
                best[candidate.code] = candidate
    order = sorted(scores, key=lambda code: (-scores[code], code))[:limit]
    return [
        best[code].model_copy(update={"rank": rank}) for rank, code in enumerate(order, start=1)
    ]


def _more_similar(candidate: HSCandidate, kept: HSCandidate) -> bool:
    if candidate.similarity is None:
        return False
    return kept.similarity is None or candidate.similarity > kept.similarity


def understand_query(description: str, prompt: TaskPrompt, client: LLMClient) -> QueryResult:
    """Rewrite an invoice description in tariff language and name likely headings."""
    values = {"description": description}
    asked = _ask(description, prompt, values, client, _parse_query_answer, QueryAnswer)
    return QueryResult(stage=asked.stage, answer=asked.parsed, calls=asked.calls)


def suggest_hs_codes(
    description: str,
    candidates: list[HSCandidate],
    prompt: TaskPrompt,
    client: LLMClient,
) -> HSResult:
    """Rerank retrieved candidates into 1-3 suggestions, or say why there are none."""
    if not candidates:
        stage = StageResult(status="failed", error="retrieval found no candidates", retryable=False)
        return HSResult(stage=stage, suggestions=[], candidates=[], calls=[])
    values = {"description": description, "candidates": format_candidates(candidates)}

    def parse(content: str) -> list[HSSuggestion]:
        return parse_rerank_answer(content, candidates)

    asked = _ask(description, prompt, values, client, parse, RerankAnswer)
    return HSResult(
        stage=asked.stage, suggestions=asked.parsed or [], candidates=candidates, calls=asked.calls
    )


def _ask[T](
    description: str,
    prompt: TaskPrompt,
    values: dict[str, str],
    client: LLMClient,
    parse: Callable[[str], T],
    schema: type[BaseModel],
) -> _Asked[T]:
    """One LLM step: ask, validate, retry once with the errors, else needs_review."""
    calls: list[LLMCall] = []
    errors: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        text = prompt.user_message(values) if attempt == 1 else prompt.retry_message(values, errors)
        try:
            response = client.complete(_request(description, prompt, text, attempt, schema))
        except SchemaViolationError as error:
            errors = [f"(whole answer): {error}"]
            calls.append(_rejected_call_record(attempt, error, errors))
            continue
        except LLMError as error:
            logger.warning("HS %s call failed", prompt.task, extra={"stage": "hs_suggest"})
            stage = StageResult(
                status="failed",
                error=f"{type(error).__name__}: {error}",
                retryable=isinstance(error, RETRYABLE_ERRORS),
                message=error.plain,
            )
            return _Asked(stage=stage, parsed=None, calls=calls)
        try:
            parsed = parse(response.content)
        except AnswerRejectedError as error:
            errors = error.errors
            calls.append(_call_record(attempt, response, errors))
            continue
        calls.append(_call_record(attempt, response, []))
        return _Asked(
            stage=StageResult(status="ok", error=None, retryable=False), parsed=parsed, calls=calls
        )
    review = StageResult(
        status="needs_review",
        error=f"{prompt.task} answer failed validation {MAX_ATTEMPTS} times",
        retryable=False,
    )
    return _Asked(stage=review, parsed=None, calls=calls)


def _load_json(content: str) -> object:
    try:
        return json.loads(content)
    except json.JSONDecodeError as error:
        raise AnswerRejectedError([f"(whole answer): not valid JSON: {error.msg}"]) from error


def _parse_query_answer(content: str) -> QueryAnswer:
    try:
        answer = QueryAnswer.model_validate(_load_json(content))
    except ValidationError as error:
        raise AnswerRejectedError(format_validation_errors(error)) from error
    bad = [
        f"headings.{position}: {heading!r} is not four digits"
        for position, heading in enumerate(answer.headings)
        if not (len(heading) == 4 and heading.isdigit())
    ]
    if bad:
        raise AnswerRejectedError(bad)
    return answer


def parse_rerank_answer(content: str, candidates: list[HSCandidate]) -> list[HSSuggestion]:
    """Validate the answer's shape, then that every code was retrieved and none repeats."""
    try:
        answer = RerankAnswer.model_validate(_load_json(content))
    except ValidationError as error:
        raise AnswerRejectedError(format_validation_errors(error)) from error
    by_code = {candidate.code: candidate for candidate in candidates}
    errors = []
    seen: set[str] = set()
    for position, choice in enumerate(answer.suggestions):
        if choice.code not in by_code:
            errors.append(f"suggestions.{position}.code: {choice.code!r} is not a candidate")
        elif choice.code in seen:
            errors.append(f"suggestions.{position}.code: {choice.code!r} is listed twice")
        seen.add(choice.code)
    if errors:
        raise AnswerRejectedError(errors)
    return [
        HSSuggestion(
            code=choice.code,
            reason=choice.reason,
            retrieval_rank=by_code[choice.code].rank,
            similarity=by_code[choice.code].similarity,
        )
        for choice in answer.suggestions
    ]


def format_candidates(candidates: list[HSCandidate]) -> str:
    return "\n".join(
        f"{candidate.rank}. {candidate.code}: {_short_path(candidate.path)}"
        for candidate in candidates
    )


def _short_path(path: str) -> str:
    parts = path.split(PATH_SEPARATOR)
    return PATH_SEPARATOR.join(
        part if len(part) <= MAX_PATH_PART_CHARS else part[: MAX_PATH_PART_CHARS - 1] + ELLIPSIS
        for part in parts
    )


def _request(
    description: str, prompt: TaskPrompt, text: str, attempt: int, schema: type[BaseModel]
) -> LLMRequest:
    # Identifies the call for caching and tracing; the full request is in the cache key.
    digest = hashlib.sha256(description.encode()).hexdigest()
    return LLMRequest(
        system=prompt.system,
        text=text,
        images=[],
        response_schema=schema.model_json_schema(),
        schema_name=schema.__name__,
        document_sha256=digest,
        prompt_version=prompt.version,
        attempt=attempt,
    )


def _call_record(attempt: int, response: LLMResponse, errors: list[str]) -> LLMCall:
    usage = response.usage
    return LLMCall(
        attempt=attempt,
        model=response.model,
        input_tokens=None if usage is None else usage.input_tokens,
        output_tokens=None if usage is None else usage.output_tokens,
        latency_ms=response.latency_ms,
        call_ms=response.call_ms,
        throttle_ms=response.throttle_ms,
        cost_usd=response.cost_usd,
        cached=response.cached,
        validation_errors=errors,
    )


def _rejected_call_record(attempt: int, error: SchemaViolationError, errors: list[str]) -> LLMCall:
    """The provider reports no usage for an output it rejected, so tokens are unknown."""
    return LLMCall(
        attempt=attempt,
        model=error.model,
        input_tokens=None,
        output_tokens=None,
        latency_ms=error.latency_ms,
        call_ms=error.call_ms,
        throttle_ms=error.throttle_ms,
        cost_usd=None,
        cached=error.cached,
        validation_errors=errors,
    )
