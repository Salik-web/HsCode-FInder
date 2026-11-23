"""CI eval gate: the comparison rules and the image-free fixture key."""

from pathlib import Path

import pytest

from app.llm.client import ImageInput, LLMRequest, NotCachedError
from evals.gate import (
    BASELINE,
    FixtureClient,
    GateScores,
    GroupScore,
    SetScore,
    compare,
    fixture_key,
)


def scores(
    accuracy: float = 0.95, scored: int = 10, caught: int = 10, flagged: int = 0
) -> GateScores:
    return GateScores(
        extraction={
            "synthetic_clean/dev": SetScore(
                documents=10,
                scored=scored,
                groups={"seen": GroupScore(documents=10, critical_field_accuracy=accuracy)},
            )
        },
        caught={"wrong_total": caught},
        clean_flagged={"synthetic_clean dev ground truth": flagged},
    )


def test_identical_scores_pass() -> None:
    assert compare(scores(), scores()) == []


def test_a_drop_within_two_points_passes_and_beyond_fails() -> None:
    assert compare(scores(accuracy=0.935), scores(accuracy=0.95)) == []
    [failure] = compare(scores(accuracy=0.92), scores(accuracy=0.95))
    assert "critical-field accuracy 92.0% vs baseline 95.0%" in failure


def test_an_improvement_passes() -> None:
    assert compare(scores(accuracy=1.0), scores(accuracy=0.95)) == []


def test_an_unscored_document_fails_with_the_refresh_instruction() -> None:
    [failure] = compare(scores(scored=9), scores())
    assert "not scored offline" in failure
    assert "--refresh" in failure


def test_guardrail_regressions_fail() -> None:
    failures = compare(scores(caught=9, flagged=1), scores())
    assert any("wrong_total caught 9 < 10" in f for f in failures)
    assert any("1 false alarms (was 0)" in f for f in failures)


def request(prompt_version: str = "v3", text: str = "page text", image: bytes = b"a") -> LLMRequest:
    return LLMRequest(
        system="s",
        text=text,
        images=[ImageInput(media_type="image/png", data=image)],
        response_schema={},
        schema_name="RawInvoice",
        document_sha256="d" * 64,
        prompt_version=prompt_version,
        attempt=1,
    )


def test_fixture_key_ignores_page_images_but_not_prompt_text_or_version() -> None:
    base = fixture_key(request(), "m", {"reasoning_effort": "none"})
    assert (
        fixture_key(request(image=b"rendered elsewhere"), "m", {"reasoning_effort": "none"}) == base
    )
    assert fixture_key(request(prompt_version="v4"), "m", {"reasoning_effort": "none"}) != base
    assert fixture_key(request(text="other text layer"), "m", {"reasoning_effort": "none"}) != base
    assert fixture_key(request(), "m", {}) != base
    assert fixture_key(request(), "other-model", {"reasoning_effort": "none"}) != base


def test_a_missing_fixture_answer_is_a_miss_never_a_call(tmp_path: Path) -> None:
    with pytest.raises(NotCachedError):
        FixtureClient("m", {}, tmp_path).complete(request())


def test_the_committed_baseline_is_valid_and_covers_every_gate_set() -> None:
    baseline = GateScores.model_validate_json(BASELINE.read_bytes())
    assert set(baseline.extraction) == {
        "synthetic_clean/dev",
        "synthetic_messy/dev",
        "synthetic_clean/test_unseen_layout",
    }
    assert all(s.scored == s.documents for s in baseline.extraction.values())
