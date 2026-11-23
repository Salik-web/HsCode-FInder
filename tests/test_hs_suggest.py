"""HS suggestion stage: retrieval-constrained rerank, one retry, explicit outcomes."""

import json

import pytest

from app.llm.client import LLMResponse, RateLimitedError, SchemaViolationError, Usage
from app.pipeline.hs_suggest import (
    MAX_PATH_PART_CHARS,
    RANKING_DEPTH,
    AnswerRejectedError,
    classify_line,
    format_candidates,
    fuse_rankings,
    parse_rerank_answer,
    retrieve_candidates,
    suggest_hs_codes,
    understand_query,
)
from app.pipeline.prompts import QUERY_TASK, RERANK_TASK, load_task_prompt
from app.schemas.hs import HSCandidate
from tests.fakes import FakeClient

PROMPT = load_task_prompt(RERANK_TASK, "v1")
QUERY_PROMPT = load_task_prompt(QUERY_TASK, "v1")
DESCRIPTION = "Men's polo shirts, knitted, 100% cotton"


def candidate(code: str, rank: int, path: str = "Shirts > Of cotton") -> HSCandidate:
    return HSCandidate(code=code, path=path, rank=rank, similarity=0.9 - rank / 100)


CANDIDATES = [candidate("6105.1000", 1), candidate("6105.2000", 2), candidate("6109.1000", 3)]


def answer(*codes: str) -> LLMResponse:
    content = json.dumps({"suggestions": [{"code": code, "reason": "fits"} for code in codes]})
    return LLMResponse(
        content=content,
        model="openai/gpt-oss-120b",
        usage=Usage(input_tokens=900, output_tokens=120),
        latency_ms=800,
        call_ms=750,
        cost_usd=None,
    )


def raw(content: str) -> LLMResponse:
    return answer().model_copy(update={"content": content})


def test_valid_answer_keeps_order_and_retrieval_signals() -> None:
    client = FakeClient("m", [answer("6105.1000", "6109.1000")])
    result = suggest_hs_codes(DESCRIPTION, CANDIDATES, PROMPT, client)
    assert result.stage.status == "ok"
    assert [s.code for s in result.suggestions] == ["6105.1000", "6109.1000"]
    assert [s.retrieval_rank for s in result.suggestions] == [1, 3]
    assert result.suggestions[1].similarity == CANDIDATES[2].similarity
    assert len(result.calls) == 1
    assert result.calls[0].input_tokens == 900


def test_request_is_text_only_and_carries_description_and_candidates() -> None:
    client = FakeClient("m", [answer("6105.1000")])
    suggest_hs_codes(DESCRIPTION, CANDIDATES, PROMPT, client)
    request = client.requests[0]
    assert request.images == []
    assert request.prompt_version == "v1"
    assert DESCRIPTION in request.text
    assert "1. 6105.1000: Shirts > Of cotton" in request.text
    assert "JSON" in request.system  # Groq's JSON object mode requires the word


def test_a_code_outside_the_candidates_is_rejected_then_retried() -> None:
    client = FakeClient("m", [answer("6105.1000", "6110.2000"), answer("6105.1000")])
    result = suggest_hs_codes(DESCRIPTION, CANDIDATES, PROMPT, client)
    assert result.stage.status == "ok"
    assert [s.code for s in result.suggestions] == ["6105.1000"]
    assert result.calls[0].validation_errors == [
        "suggestions.1.code: '6110.2000' is not a candidate"
    ]
    assert "'6110.2000' is not a candidate" in client.requests[1].text
    assert client.requests[1].attempt == 2


def test_two_rejected_answers_mean_needs_review_with_no_suggestions() -> None:
    client = FakeClient("m", [answer("9999.9999"), answer("6105.1000", "6105.1000")])
    result = suggest_hs_codes(DESCRIPTION, CANDIDATES, PROMPT, client)
    assert result.stage.status == "needs_review"
    assert result.suggestions == []
    assert result.candidates == CANDIDATES  # retrieval is kept for the reviewer
    assert result.calls[1].validation_errors == ["suggestions.1.code: '6105.1000' is listed twice"]


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ("not json", "(whole answer): not valid JSON"),
        ('{"suggestions": []}', "suggestions: List should have at least 1 item"),
        ('{"suggestions": [{"code": "6105.1000"}]}', "suggestions.0.reason: Field required"),
        (
            '{"suggestions": [{"code": "6105.1000", "reason": "x"}], "confidence": 0.9}',
            "confidence: Extra inputs are not permitted",
        ),
    ],
)
def test_malformed_answers_are_rejected_with_a_field_path(content: str, error: str) -> None:
    with pytest.raises(AnswerRejectedError) as raised:
        parse_rerank_answer(content, CANDIDATES)
    assert raised.value.errors[0].startswith(error)


def test_more_than_three_suggestions_is_rejected() -> None:
    four = [candidate(f"6105.{n}000", n) for n in range(1, 5)]
    with pytest.raises(AnswerRejectedError, match="at most 3"):
        parse_rerank_answer(answer(*[c.code for c in four]).content, four)


def test_provider_schema_rejection_counts_as_a_failed_attempt() -> None:
    rejection = SchemaViolationError("groq rejected", model="m", latency_ms=300, call_ms=280)
    client = FakeClient("m", [rejection, answer("6105.2000")])
    result = suggest_hs_codes(DESCRIPTION, CANDIDATES, PROMPT, client)
    assert result.stage.status == "ok"
    assert result.calls[0].latency_ms == 300
    assert result.calls[0].input_tokens is None


def test_rate_limit_fails_the_stage_as_retryable_and_keeps_candidates() -> None:
    client = FakeClient("m", [RateLimitedError("quota")])
    result = suggest_hs_codes(DESCRIPTION, CANDIDATES, PROMPT, client)
    assert result.stage.status == "failed"
    assert result.stage.retryable is True
    assert result.candidates == CANDIDATES


def test_no_candidates_fails_without_calling_the_model() -> None:
    client = FakeClient("m", [])
    result = suggest_hs_codes(DESCRIPTION, [], PROMPT, client)
    assert result.stage.status == "failed"
    assert result.stage.retryable is False
    assert client.requests == []


def test_long_path_levels_are_shortened_for_the_prompt() -> None:
    long_level = "x" * 1000
    listing = format_candidates([candidate("8708.2911", 1, f"Parts > {long_level} > Other")])
    shortened = listing.split(" > ")[1]
    assert len(shortened) == MAX_PATH_PART_CHARS
    assert listing.endswith(" > Other")


class FakeEmbedder:
    def embed_query(self, text: str) -> list[float]:
        return [float(len(text))]


class FakeIndex:
    """Vector search answers by query length; full-text search from a fixed table."""

    def __init__(self, vector: dict[float, list[str]], lexical: dict[str, list[str]]) -> None:
        self.vector = vector
        self.lexical = lexical
        self.limits: list[int] = []

    def search(self, query_vector: list[float], limit: int) -> list[HSCandidate]:
        self.limits.append(limit)
        codes = self.vector.get(query_vector[0], [])
        return [candidate(code, rank) for rank, code in enumerate(codes, start=1)]

    def search_text(self, query: str, limit: int) -> list[HSCandidate]:
        self.limits.append(limit)
        codes = self.lexical.get(query, [])
        return [
            candidate(code, rank).model_copy(update={"similarity": None})
            for rank, code in enumerate(codes, start=1)
        ]


def test_fusion_rewards_agreement_and_renumbers_ranks() -> None:
    first = [candidate("6105.1000", 1), candidate("6109.1000", 2)]
    second = [candidate("6109.1000", 1), candidate("6110.2000", 2)]
    fused = fuse_rankings([first, second], limit=10)
    assert [c.code for c in fused] == ["6109.1000", "6105.1000", "6110.2000"]
    assert [c.rank for c in fused] == [1, 2, 3]


def test_fusion_keeps_the_vector_similarity_over_a_full_text_none() -> None:
    lexical = [candidate("6105.1000", 1).model_copy(update={"similarity": None})]
    vector = [candidate("6105.1000", 3)]
    [fused] = fuse_rankings([lexical, vector], limit=10)
    assert fused.similarity == vector[0].similarity
    [only_text] = fuse_rankings([lexical], limit=10)
    assert only_text.similarity is None


def test_fusion_ties_break_by_code_and_respect_the_limit() -> None:
    fused = fuse_rankings([[candidate("6109.1000", 1)], [candidate("6105.1000", 1)]], limit=1)
    assert [c.code for c in fused] == ["6105.1000"]


def test_retrieval_fuses_vector_and_text_for_description_and_rewrite() -> None:
    rewrite = "Shirts, knitted, of cotton"
    index = FakeIndex(
        vector={float(len(DESCRIPTION)): ["6109.1000"], float(len(rewrite)): ["6105.1000"]},
        lexical={DESCRIPTION: ["6110.2000"], rewrite: ["6105.1000"]},
    )
    found = retrieve_candidates(DESCRIPTION, rewrite, FakeEmbedder(), index)
    assert found[0].code == "6105.1000"  # in two rankings
    assert {c.code for c in found} == {"6105.1000", "6109.1000", "6110.2000"}
    assert index.limits == [RANKING_DEPTH] * 4


def test_retrieval_without_a_rewrite_uses_the_description_only() -> None:
    index = FakeIndex(vector={float(len(DESCRIPTION)): ["6109.1000"]}, lexical={})
    found = retrieve_candidates(DESCRIPTION, None, FakeEmbedder(), index)
    assert [c.code for c in found] == ["6109.1000"]
    assert len(index.limits) == 2


def query_answer(rewrite: str = "Shirts, knitted, of cotton", headings=("6105",)) -> LLMResponse:
    content = json.dumps({"tariff_description": rewrite, "headings": list(headings)})
    return raw(content)


def test_query_understanding_returns_the_rewrite() -> None:
    client = FakeClient("m", [query_answer()])
    result = understand_query(DESCRIPTION, QUERY_PROMPT, client)
    assert result.stage.status == "ok"
    assert result.answer is not None
    assert result.answer.tariff_description == "Shirts, knitted, of cotton"
    assert DESCRIPTION in client.requests[0].text


def test_query_headings_must_be_four_digits_then_retry() -> None:
    client = FakeClient("m", [query_answer(headings=("61.05",)), query_answer()])
    result = understand_query(DESCRIPTION, QUERY_PROMPT, client)
    assert result.stage.status == "ok"
    assert result.calls[0].validation_errors == ["headings.0: '61.05' is not four digits"]


def test_a_failed_query_step_falls_back_to_the_description() -> None:
    index = FakeIndex(vector={float(len(DESCRIPTION)): ["6105.1000"]}, lexical={})
    client = FakeClient("m", [raw("nope"), raw("still nope"), answer("6105.1000")])
    line = classify_line(DESCRIPTION, FakeEmbedder(), index, QUERY_PROMPT, PROMPT, client)
    assert line.query.stage.status == "needs_review"
    assert line.hs.stage.status == "ok"
    assert [s.code for s in line.hs.suggestions] == ["6105.1000"]


def test_a_rate_limited_query_step_stops_the_line_before_retrieval() -> None:
    index = FakeIndex(vector={}, lexical={})
    client = FakeClient("m", [RateLimitedError("quota")])
    line = classify_line(DESCRIPTION, FakeEmbedder(), index, QUERY_PROMPT, PROMPT, client)
    assert line.hs.stage.status == "failed"
    assert line.hs.stage.retryable is True
    assert index.limits == []
