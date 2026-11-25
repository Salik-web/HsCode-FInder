"""HS codes printed at the end of a description, and their comparison with suggestions."""

import json
from pathlib import Path

import pytest

from app.pipeline.printed_hs import printed_code_mismatch, same_code, split_printed_code
from app.schemas.hs import HSSuggestion
from synth.generator import load_catalogue
from tariff.parse import load_hs_code_table

HS_GOLD = Path(__file__).parents[1] / "evals" / "datasets" / "hs_gold.jsonl"


def suggestion(code: str) -> HSSuggestion:
    return HSSuggestion(code=code, reason="r", retrieval_rank=1, similarity=0.7)


# --- taking a code off a description ------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "on_doc", "goods", "code"),
    [
        # The code in the description cell and in hs_code_on_doc (a layout seen on a real form).
        ("Cotton shirts - 6105.10.4000.8", "6105.10.4000.8", "Cotton shirts", None),
        ("Cotton shirts\n6105.10.4000.8", "6105.10.4000.8", "Cotton shirts", None),
        ("Cotton shirts 6105104000", "6105.10.4000.8", "Cotton shirts", None),
        ("Cotton shirts 6105.10", "61051040", "Cotton shirts", None),
        # Only in the description.
        ("Cotton towels - 6302.60.0010.3", None, "Cotton towels", "6302.60.0010.3"),
        ("Cotton towels 6302.60.0010", None, "Cotton towels", "6302.60.0010"),
        ("Cotton yarn, combed (5205.12)", None, "Cotton yarn, combed", "5205.12"),
        ("Cotton yarn \u2013 5205.1200", None, "Cotton yarn", "5205.1200"),
        ("Cotton yarn HS Code 52051200", None, "Cotton yarn", "52051200"),
        ("Cotton yarn, HS: 5205 12 00", None, "Cotton yarn", "5205 12 00"),
        ("Cotton yarn, HS heading 5205", None, "Cotton yarn", "5205"),
        ("Cotton yarn (H.S. 5205.12)", None, "Cotton yarn", "5205.12"),
        ("Cotton yarn HTS 5205.12.1000", None, "Cotton yarn", "5205.12.1000"),
        ("Cotton yarn - Tariff No. 5205.12", None, "Cotton yarn", "5205.12"),
    ],
)
def test_a_code_at_the_end_is_taken_off(
    description: str, on_doc: str | None, goods: str, code: str | None
) -> None:
    split = split_printed_code(description, on_doc, load_hs_code_table().headings)
    assert split.description == goods
    assert split.code is not None
    if code is not None:
        assert split.code == code


@pytest.mark.parametrize(
    "description",
    [
        "LED panel lights 40W",
        "Radial car tyres 205/55 R16",
        "Cotton yarn, combed, 30s count",
        "SPLIT AC 1.5 TON",
        "HR COIL SS400 2.0MM X 1250MM, PRIME",
        "Bearing 6204",
        "Bearing 6204 2RS",
        "Pump model HS 2000",  # chapter 20 has no heading 2000
        "Pump model HS 8413",  # a label alone doesn't make four digits a code
        "Cable drum - 1250.50",  # no heading 1250
        "Pipe 1250.50.30",
        "Batch 2026.11.25",
        "Batch - 2026.11.25",
        "Resistor 5205.12",  # two groups and no punctuation: it could be a plain number
        "6105.10.4000",  # nothing else: not a description
        "",
    ],
)
def test_anything_else_stays(description: str) -> None:
    headings = load_hs_code_table().headings
    assert split_printed_code(description, None, headings).code is None
    assert split_printed_code(description, None, headings).description == description


def test_a_model_number_isnt_the_start_of_the_lines_own_code() -> None:
    assert split_printed_code("Bearing 6204", "6204.11.00").code is None


def test_no_hs_gold_description_changes() -> None:
    """The HS eval's requests, and so its cached answers, stay exactly as measured."""
    headings = load_hs_code_table().headings
    for line in HS_GOLD.read_text(encoding="utf-8").splitlines():
        description = json.loads(line)["description"]
        assert split_printed_code(description, None, headings).description == description
        assert split_printed_code(description, None).description == description


def test_no_synthetic_description_changes() -> None:
    for item in load_catalogue():
        assert split_printed_code(item.description, item.hs_code).description == item.description


# --- comparing a printed code with our suggestion --------------------------------------------


@pytest.mark.parametrize(
    ("code", "other", "same"),
    [
        ("6105.10.4000.8", "61051040008", True),
        ("6105.10", "6105.10.4000.8", True),
        ("6105.10", "6105.90", False),
        ("61", "6105.10", False),
        ("6105.10", None, False),
    ],
)
def test_same_code(code: str, other: str | None, same: bool) -> None:
    assert same_code(code, other) is same


def test_a_different_code_at_six_digits_is_a_mismatch_with_both_codes() -> None:
    found = printed_code_mismatch("6105.10.4000.8", [suggestion("6109.1000")])
    assert found is not None
    assert (found.printed, found.suggested, found.digits) == ("6105.10.4000.8", "6109.1000", 6)


@pytest.mark.parametrize(
    ("printed", "suggested"),
    [
        ("6105.10.4000.8", ["6105.1010"]),  # national digits differ, the first 6 agree
        ("6105", ["6105.1010"]),  # compared as far as printed
        ("61", ["6109.1000"]),  # too short to compare
        (None, ["6109.1000"]),
        ("6105.10", []),  # nothing suggested: the HS stage already says why
    ],
)
def test_no_mismatch(printed: str | None, suggested: list[str]) -> None:
    assert printed_code_mismatch(printed, [suggestion(code) for code in suggested]) is None


def test_only_the_top_suggestion_counts() -> None:
    assert printed_code_mismatch("6105.10", [suggestion("6109.1000"), suggestion("6105.1010")])


def test_a_printed_heading_is_compared_at_four_digits() -> None:
    found = printed_code_mismatch("6105", [suggestion("6109.1000")])
    assert found is not None
    assert found.digits == 4
