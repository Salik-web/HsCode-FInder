"""HS metrics: levels, top-k, misses counted, calibration buckets, usage totals."""

import json
from pathlib import Path

import pytest

from app.schemas.result import LLMCall
from evals.hs_metrics import HSItemOutcome, hit_at, load_hs_gold, same_at, summarise_hs


def call(errors: list[str] | None = None, tokens: int | None = 100) -> LLMCall:
    return LLMCall(
        attempt=1,
        model="m",
        input_tokens=tokens,
        output_tokens=None if tokens is None else tokens // 10,
        latency_ms=900,
        call_ms=800,
        cost_usd=None,
        cached=False,
        validation_errors=errors or [],
    )


def outcome(
    gold: str,
    suggestions: list[str],
    candidates: list[str] | None = None,
    status: str = "ok",
    similarity: float | None = 0.75,
    calls: list[LLMCall] | None = None,
) -> HSItemOutcome:
    return HSItemOutcome(
        id="hs",
        gold_code=gold,
        status=status,
        candidates=candidates if candidates is not None else suggestions,
        suggestions=suggestions,
        top_similarity=similarity if suggestions else None,
        calls=calls if calls is not None else [call()],
        query_status="ok",
        query_calls=[],
    )


@pytest.mark.parametrize(
    ("level", "expected"), [("heading", True), ("subheading", True), ("national", False)]
)
def test_same_at_compares_4_6_and_8_digits(level: str, expected: bool) -> None:
    assert same_at(level, "6105.1000", "6105.1090") is expected


def test_same_at_heading_differs_on_the_fourth_digit() -> None:
    assert not same_at("heading", "6105.1000", "6106.1000")


def test_hit_at_looks_only_at_the_first_k() -> None:
    ranked = ["6109.1000", "6105.2000", "6105.1000"]
    assert not hit_at("national", "6105.1000", ranked, 2)
    assert hit_at("national", "6105.1000", ranked, 3)
    assert hit_at("national", "6105.1000", ranked, None)
    assert not hit_at("national", "6105.1000", [], 3)


def test_needs_review_and_failed_count_as_misses() -> None:
    summary = summarise_hs(
        [
            outcome("6105.1000", ["6105.1000"]),
            outcome("6105.1000", [], ["6105.1000"], status="needs_review"),
            outcome("6105.1000", [], ["6105.1000"], status="failed", calls=[]),
        ]
    )
    assert summary.rerank_top1["national"].hits == 1
    assert summary.rerank_top1["national"].total == 3
    # Retrieval found gold every time; the rerank stage lost two.
    assert summary.retrieval_recall["national"].hits == 3
    assert summary.statuses == {"ok": 1, "needs_review": 1, "failed": 1}


def test_rerank_and_retrieval_order_are_scored_separately() -> None:
    summary = summarise_hs(
        [outcome("6105.1000", ["6105.1000"], ["6109.1000", "6105.2000", "6110.1000", "6105.1000"])]
    )
    assert summary.rerank_top1["national"].hits == 1
    assert summary.retrieval_top1["national"].hits == 0
    assert summary.retrieval_top3["subheading"].hits == 0
    assert summary.retrieval_top3["heading"].hits == 1
    assert summary.retrieval_recall["national"].hits == 1


def test_first_try_valid_and_retries_count_reranked_items_only() -> None:
    summary = summarise_hs(
        [
            outcome("6105.1000", ["6105.1000"], calls=[call(["bad code"]), call()]),
            outcome("6105.1000", ["6105.1000"]),
            outcome("6105.1000", [], status="failed", calls=[]),
        ]
    )
    assert (summary.first_try_valid.hits, summary.first_try_valid.total) == (1, 2)
    assert summary.retries == 1


def test_calibration_buckets_by_first_suggestion_similarity() -> None:
    summary = summarise_hs(
        [
            outcome("6105.1000", ["6105.1000"], similarity=0.82),
            outcome("6105.1000", ["6109.1000"], similarity=0.85),
            outcome("6105.1000", ["6105.1000"], similarity=0.55),
            outcome("6105.1000", [], status="needs_review"),
        ]
    )
    by_low = {bucket.low: bucket.national_top1 for bucket in summary.calibration}
    assert (by_low[0.8].hits, by_low[0.8].total) == (1, 2)
    assert (by_low[0.0].hits, by_low[0.0].total) == (1, 1)
    assert by_low[0.7].total == 0
    assert by_low[0.7].value is None  # no data is not a zero score


def test_tokens_are_unknown_when_any_call_lacks_usage() -> None:
    known = summarise_hs([outcome("6105.1000", ["6105.1000"])])
    assert (known.input_tokens, known.output_tokens) == (100, 10)
    unknown = summarise_hs(
        [outcome("6105.1000", ["6105.1000"]), outcome("6105.1000", [], calls=[call(tokens=None)])]
    )
    assert unknown.input_tokens is None


def test_empty_run_has_no_rates() -> None:
    summary = summarise_hs([])
    assert summary.rerank_top3["national"].value is None
    assert summary.call_ms_p95 is None


def test_load_hs_gold_filters_by_split(tmp_path: Path) -> None:
    path = tmp_path / "gold.jsonl"
    rows = [
        {
            "id": "a",
            "description": "x",
            "gold_code": "6105.1000",
            "split": "dev",
            "verified": False,
        },
        {
            "id": "b",
            "description": "y",
            "gold_code": "6109.1000",
            "split": "test",
            "verified": True,
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    assert [item.id for item in load_hs_gold(path, "dev")] == ["a"]
    assert len(load_hs_gold(path, None)) == 2


def test_committed_hs_gold_is_valid_and_split() -> None:
    items = load_hs_gold(Path("evals/datasets/hs_gold.jsonl"), None)
    assert len(items) == len({item.id for item in items})
    assert {item.split for item in items} == {"dev", "test"}


def test_every_gold_code_is_a_real_pct_code() -> None:
    """Gold must come from the parsed tariff, never from the placeholder catalogue."""
    from tariff.parse import read_csv

    codes = {row.code for row in read_csv(Path("tariff/pct_2026-27.csv"))}
    items = load_hs_gold(Path("evals/datasets/hs_gold.jsonl"), None)
    assert [item.gold_code for item in items if item.gold_code not in codes] == []


def test_query_calls_count_toward_tokens_and_query_ok() -> None:
    item = outcome("6105.1000", ["6105.1000"]).model_copy(
        update={"query_calls": [call(tokens=50)], "query_status": "needs_review"}
    )
    summary = summarise_hs([item, outcome("6105.1000", ["6105.1000"])])
    assert summary.input_tokens == 250
    assert (summary.query_ok.hits, summary.query_ok.total) == (1, 2)
