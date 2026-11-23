"""Review logic (app/pipeline/review.py): status, corrections, filenames, export."""

import csv
import io
import json
from decimal import Decimal

import pytest

from app.pipeline.hs_suggest import HSLineResult, HSResult, QueryResult
from app.pipeline.review import (
    CorrectionInput,
    InvalidCorrectionError,
    apply_corrections,
    export_csv,
    export_json,
    field_value,
    safe_filename,
    status_after_run,
)
from app.schemas.raw import RawInvoice
from app.schemas.result import FieldAssessment, PipelineResult, StageResult
from synth.generator import generate_ground_truth, load_catalogue

OK = StageResult(status="ok", error=None, retryable=False)
REVIEW = StageResult(status="needs_review", error="x", retryable=False)
FAILED = StageResult(status="failed", error="RateLimitedError", retryable=True)


def invoice() -> RawInvoice:
    return generate_ground_truth(42, 5, load_catalogue()).invoice


def result(
    extracted: RawInvoice | None = None,
    extract: StageResult = OK,
    statuses: tuple[str, ...] = ("ok",),
) -> PipelineResult:
    return PipelineResult(
        document_sha256="a" * 64,
        prompt_version="v3",
        model="m",
        page_count=1,
        preprocess=OK,
        extract=extract,
        invoice=extracted,
        llm_calls=[],
        rules_applied=[],
        field_checks=[],
        assessments=[
            FieldAssessment(path=f"f{i}", confidence=0.9, status=s, reasons=[])
            for i, s in enumerate(statuses)
        ],
    )


def hs_line(status: StageResult = OK) -> HSLineResult:
    return HSLineResult(
        query=QueryResult(stage=OK, answer=None, calls=[]),
        hs=HSResult(stage=status, suggestions=[], candidates=[], calls=[]),
    )


def edit(path: str, value: object) -> CorrectionInput:
    return CorrectionInput(field_path=path, new_value=value)


# --- status -------------------------------------------------------------------------------


def test_every_field_ok_and_every_hs_step_ok_is_ready() -> None:
    assert status_after_run(result(invoice()), [hs_line()]) == "ready"


def test_one_field_in_review_or_one_hs_step_not_ok_needs_review() -> None:
    assert (
        status_after_run(result(invoice(), statuses=("ok", "failed_check")), []) == "needs_review"
    )
    assert status_after_run(result(invoice()), [hs_line(), hs_line(FAILED)]) == "needs_review"


def test_no_invoice_is_failed_unless_extraction_asked_for_review() -> None:
    assert status_after_run(result(None, extract=FAILED, statuses=()), []) == "failed"
    assert status_after_run(result(None, extract=REVIEW, statuses=()), []) == "needs_review"


# --- corrections --------------------------------------------------------------------------


def test_corrections_apply_in_order_to_header_party_and_line_fields() -> None:
    original = invoice()
    corrected = apply_corrections(
        original,
        [
            edit("invoice_number", "INV-1"),
            edit("invoice_number", "INV-2"),
            edit("buyer.name", "Acme Traders"),
            edit("line_items[0].amount", "12.50"),
        ],
    )
    assert corrected.invoice_number == "INV-2"
    assert corrected.buyer.name == "Acme Traders"
    assert corrected.line_items[0].amount == Decimal("12.50")
    assert original.invoice_number != "INV-2"  # the model's answer is never changed


def test_a_correction_can_clear_a_field() -> None:
    assert apply_corrections(invoice(), [edit("incoterm", None)]).incoterm is None


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        ("invoice_numbr", "x", "no such field"),
        ("buyer", "x", "not a single field"),
        ("line_items", "x", "not a single field"),
        ("line_items[999].amount", "1", "no such field"),
        ("line_items[0]", "1", "no such field"),
        ("buyer.name.first", "x", "no such field"),
        ("total_amount", "a lot", "total_amount"),
        ("incoterm", "XYZ", "incoterm"),
        ("invoice_date", "31/31/2026", "invoice_date"),
    ],
)
def test_bad_corrections_are_rejected(path: str, value: object, message: str) -> None:
    with pytest.raises(InvalidCorrectionError, match=message):
        apply_corrections(invoice(), [edit(path, value)])


def test_a_missing_notify_party_reads_as_none_and_a_correction_adds_it() -> None:
    """Regression: the review screen crashed reading notify_party.* on such a document."""
    without_notify = invoice().model_copy(update={"notify_party": None})
    assert field_value(without_notify, "notify_party.name") is None
    added = apply_corrections(without_notify, [edit("notify_party.name", "Clearing Agent Ltd")])
    assert added.notify_party is not None
    assert added.notify_party.name == "Clearing Agent Ltd"
    assert added.notify_party.address is None


def test_field_value_reads_the_current_value() -> None:
    source = invoice()
    assert field_value(source, "buyer.name") == source.buyer.name
    # Values are read as the JSON the corrections store: Decimals as strings.
    assert field_value(source, "line_items[1].quantity") == str(source.line_items[1].quantity)


# --- filenames ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("invoice 42.pdf", "invoice_42.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\x\\scan.JPG", "scan.JPG"),
        ("", "document"),
        ("...", "document"),
    ],
)
def test_filenames_are_sanitised_for_display(name: str, expected: str) -> None:
    assert safe_filename(name) == expected


# --- export -------------------------------------------------------------------------------


def test_csv_has_one_row_per_line_with_exact_decimals_and_chosen_codes() -> None:
    source = invoice()
    rows = list(csv.DictReader(io.StringIO(export_csv(source, {0: "6109.1000"}))))
    assert len(rows) == len(source.line_items)
    assert rows[0]["hs_code_chosen"] == "6109.1000"
    assert rows[1]["hs_code_chosen"] == ""
    assert rows[0]["amount"] == format(source.line_items[0].amount, "f")
    assert rows[0]["invoice_number"] == source.invoice_number
    assert rows[1]["line"] == "2"


def test_csv_neutralises_spreadsheet_formulas_from_the_document() -> None:
    line = invoice().line_items[0].model_copy(update={"description": '=HYPERLINK("x")'})
    hostile = invoice().model_copy(update={"line_items": [line]})
    [row] = csv.DictReader(io.StringIO(export_csv(hostile, {})))
    assert row["description"] == '\'=HYPERLINK("x")'


def test_json_export_adds_chosen_codes_and_provenance() -> None:
    exported = export_json(invoice(), {0: "6109.1000"}, {"run_id": 7})
    assert exported["invoice"]["line_items"][0]["hs_code_chosen"] == "6109.1000"
    assert exported["invoice"]["line_items"][1]["hs_code_chosen"] is None
    assert exported["provenance"] == {"run_id": 7}
    json.dumps(exported)  # serialisable as it stands
