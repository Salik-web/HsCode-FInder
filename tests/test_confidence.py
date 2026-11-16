"""Confidence scoring (app/pipeline/confidence.py): every signal, the threshold, the reasons."""

import random
from datetime import date
from decimal import Decimal

import pytest

from app.pipeline.confidence import (
    REVIEW_THRESHOLD,
    PageText,
    assess_fields,
    date_renderings,
    field_values,
    normalise,
    numbers_in,
)
from app.pipeline.preprocess import prepare_document
from app.pipeline.rules import REFERENCE_RULE, describe_change, parse_change
from app.schemas.raw import RawInvoice
from app.schemas.result import FieldAssessment, FieldCheck, LLMCall
from synth.generator import generate_ground_truth, load_catalogue
from synth.layouts import DATE_FORMATS, TEMPLATES, pick_labels
from synth.render import open_browser_page, render_html, render_pdf
from tests.test_metrics import line, make_invoice, party


def call(attempt: int = 1, **changes: object) -> LLMCall:
    values: dict[str, object] = {
        "attempt": attempt,
        "model": "m",
        "input_tokens": 1,
        "output_tokens": 1,
        "latency_ms": 1,
        "cost_usd": Decimal(0),
        "cached": True,
        "validation_errors": [],
    }
    return LLMCall(**(values | changes))


def page_for(invoice: RawInvoice) -> str:
    """A text layer that prints every value, roughly as a layout would."""
    parts = [f"Invoice {invoice.invoice_number}", invoice.invoice_date.strftime("%d %b %Y")]
    for name in ("shipper", "buyer", "consignee"):
        p = getattr(invoice, name)
        parts.append(f"{p.name}\n{p.address}\nPakistan")
    parts.append(f"Terms: {invoice.incoterm} Currency: {invoice.currency}")
    for item in invoice.line_items:
        parts.append(f"{item.description} {item.unit} {item.unit_price:,.2f} {item.quantity}")
        parts.append(f"{item.amount:,.2f}")
    parts.append(f"Net {invoice.net_weight_kg} kg Gross {invoice.gross_weight_kg} kg")
    parts.append(f"Total {invoice.currency} {invoice.total_amount:,.2f}")
    return "\n".join(parts)


def by_path(assessments: list[FieldAssessment]) -> dict[str, FieldAssessment]:
    return {assessment.path: assessment for assessment in assessments}


def assess(
    invoice: RawInvoice,
    text: str | None,
    calls: list[LLMCall] | None = None,
    rules: list[str] | None = None,
    checks: list[FieldCheck] | None = None,
) -> dict[str, FieldAssessment]:
    return by_path(assess_fields(invoice, text, calls or [call()], rules or [], checks or []))


# --- paths ---------------------------------------------------------------------------------


def test_every_field_has_one_path() -> None:
    invoice = make_invoice()
    values = field_values(invoice)
    assert len(values) == 7 + 4 * 3 + 6 * len(invoice.line_items)
    assert values["notify_party.name"] is None  # absent party
    assert values["line_items[1].amount"] == Decimal("50.50")
    assert values["buyer.country"] == "PK"


# --- reading the page ----------------------------------------------------------------------


def test_normalise_ignores_case_punctuation_and_spacing() -> None:
    assert normalise("  Tawkeel,  Abaan & GULSHAN\n") == "tawkeel abaan gulshan"
    assert normalise("İçtaş İnşaat") == normalise("İçtaş İnşaat")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Total USD 12,129.84", {Decimal("12129.84")}),
        ("Summe 1.234,56", {Decimal("1234.56")}),
        ("Qty 36 pcs.", {Decimal("36")}),
        ("Net Wt. 648.00 kg.", {Decimal("648.00")}),
        ("1,234", {Decimal("1234"), Decimal("1.234")}),  # ambiguous: both readings
        ("H.No. 4-3", {Decimal("4"), Decimal("3")}),
    ],
)
def test_numbers_on_the_page_are_read_as_numbers(text: str, expected: set[Decimal]) -> None:
    assert expected <= numbers_in(text)


@pytest.mark.parametrize("layout_format", sorted(set(DATE_FORMATS.values())))
def test_every_layout_date_format_is_recognised(layout_format: str) -> None:
    day = date(2026, 9, 5)
    assert (
        PageText(f"Dated {day.strftime(layout_format)}").agreement("invoice_date", day) == "found"
    )


@pytest.mark.parametrize("printed", ["05/09/2026", "09/05/2026", "2026-09-05", "5 Sept. 2026"])
def test_common_date_formats_beyond_the_layouts_are_recognised(printed: str) -> None:
    found = PageText(f"Date: {printed}").agreement("invoice_date", date(2026, 9, 5))
    assert found == ("not_found" if "Sept" in printed else "found")


def test_a_different_date_is_not_found() -> None:
    assert PageText("Date: 05 Sep 2026").agreement("invoice_date", date(2026, 9, 6)) == "not_found"
    assert len(date_renderings(date(2026, 9, 5))) == len(set(date_renderings(date(2026, 9, 5))))


def test_text_must_match_whole_words() -> None:
    page = PageText("Omair Industries Ltd")
    assert page.agreement("buyer.name", "Omair Industries") == "found"
    assert page.agreement("buyer.name", "Omai") == "not_found"


def test_none_and_country_codes_are_not_checked() -> None:
    page = PageText("Pakistan")
    assert page.agreement("buyer.country", "PK") == "not_checked"
    assert page.agreement("gross_weight_kg", None) == "not_checked"


# --- scores --------------------------------------------------------------------------------


def test_a_correct_extraction_on_its_own_page_is_trusted() -> None:
    invoice = make_invoice()
    scores = assess(invoice, page_for(invoice))
    assert scores["total_amount"].confidence == 0.95
    assert scores["buyer.name"].confidence == 0.95
    assert scores["buyer.country"].confidence == 0.75  # not checked
    assert scores["notify_party.name"].confidence == 0.75  # None: nothing to look for
    assert {a.status for a in scores.values()} == {"ok"}


def test_a_value_missing_from_the_text_layer_goes_to_review() -> None:
    invoice = make_invoice()
    misread = invoice.model_copy(update={"total_amount": Decimal("205.50")})
    total = assess(misread, page_for(invoice))["total_amount"]
    assert total.confidence == 0.30
    assert total.status == "needs_review"
    assert total.reasons == ["text layer: base 0.75", "not found in text layer -0.45"]


def test_without_a_text_layer_every_field_starts_at_the_image_base() -> None:
    scores = assess(make_invoice(), None)
    assert {a.confidence for a in scores.values()} == {0.60}
    assert {a.status for a in scores.values()} == {"ok"}


def test_a_retry_and_repaired_keys_lower_every_field() -> None:
    invoice = make_invoice()
    calls = [call(1, validation_errors=["x"]), call(2, keys_repaired=["a -> b"])]
    scores = assess(invoice, page_for(invoice), calls)
    assert scores["total_amount"].confidence == 0.80  # 0.95 - 0.10 - 0.05
    assert scores["buyer.country"].confidence == 0.60


def test_a_reinterpreted_number_lowers_only_that_field() -> None:
    invoice = make_invoice()
    calls = [call(numbers_normalised=["line_items[0].amount"])]
    scores = assess(invoice, page_for(invoice), calls)
    assert scores["line_items[0].amount"].confidence == 0.85
    assert scores["line_items[1].amount"].confidence == 0.95


def test_a_value_changed_by_a_rule_is_looked_up_as_printed() -> None:
    invoice = make_invoice()
    page = page_for(invoice).replace(" PCS ", " PIECES ", 1)
    change = describe_change("line_items[0].unit", "PIECES", "PCS")
    scores = assess(invoice, page, rules=[change])
    assert scores["line_items[0].unit"].confidence == 0.90  # found as printed, set by a rule
    assert scores["line_items[0].unit"].status == "ok"


def test_a_buyer_resolved_from_a_reference_is_marked_set_by_rule() -> None:
    invoice = make_invoice()
    scores = assess(invoice, page_for(invoice), rules=[REFERENCE_RULE])
    assert scores["buyer.name"].confidence == 0.90
    assert scores["consignee.name"].confidence == 0.95


def test_a_failed_check_caps_confidence_and_sets_failed_check() -> None:
    invoice = make_invoice()
    check = FieldCheck(path="total_amount", status="failed_check", rule="invoice_total", detail="x")
    total = assess(invoice, page_for(invoice), checks=[check])["total_amount"]
    assert (total.confidence, total.status) == (0.10, "failed_check")
    assert total.reasons[-1] == "guardrail check failed: capped at 0.10"


def test_the_threshold_is_exclusive() -> None:
    """0.60 - 0.10 = 0.50 stays ok; 0.45 goes to review."""
    assert REVIEW_THRESHOLD == 0.50
    retried = [call(1, validation_errors=["x"]), call(2)]
    assert assess(make_invoice(), None, retried)["currency"].status == "ok"
    repaired = [call(1, validation_errors=["x"]), call(2, keys_ignored=["notes"])]
    currency = assess(make_invoice(), None, repaired)["currency"]
    assert (currency.confidence, currency.status) == (0.45, "needs_review")


def test_confidence_stays_between_zero_and_one() -> None:
    invoice = make_invoice()
    misread = invoice.model_copy(update={"total_amount": Decimal("1.11")})
    calls = [
        call(1, validation_errors=["x"]),
        call(2, keys_repaired=["a"], numbers_normalised=["total_amount"]),
    ]
    total = assess(misread, page_for(invoice), calls)["total_amount"]
    assert total.confidence == 0.05  # 0.75 - 0.45 - 0.10 - 0.05 - 0.10
    assert all(0 <= a.confidence <= 1 for a in assess(misread, page_for(invoice), calls).values())


def test_rule_changes_round_trip() -> None:
    change = describe_change("line_items[3].unit", "it's", "PCS")
    parsed = parse_change(change)
    assert parsed is not None
    assert (parsed.path, parsed.printed) == ("line_items[3].unit", "it's")
    assert parse_change(REFERENCE_RULE) is None


# --- real pages ----------------------------------------------------------------------------


@pytest.mark.parametrize("template", TEMPLATES)
def test_every_value_of_a_correct_extraction_is_found_on_a_rendered_page(template: str) -> None:
    """True positives on real PDFs: PDFium's text layer, after the hyphen fix."""
    catalogue = load_catalogue()
    truths = [generate_ground_truth(42, index, catalogue) for index in range(4)]
    with open_browser_page() as page:
        for truth in truths:
            labels = pick_labels(random.Random(1))
            html = render_html(truth.invoice, template, labels, truth.buyer_reference)
            document = prepare_document(render_pdf(page, html))
            rules = [REFERENCE_RULE] if truth.buyer_reference else []
            scores = assess_fields(truth.invoice, document.text_layer, [call()], rules, [])
            missing = [a.path for a in scores if "not found in text layer -0.45" in a.reasons]
            assert missing == [], (template, missing)


def test_a_misread_value_on_a_rendered_page_is_not_found() -> None:
    truth = generate_ground_truth(42, 0, load_catalogue())
    with open_browser_page() as page:
        html = render_html(truth.invoice, "classic", pick_labels(random.Random(1)))
        document = prepare_document(render_pdf(page, html))
    assert truth.invoice.total_amount is not None
    misread = truth.invoice.model_copy(
        update={
            "total_amount": truth.invoice.total_amount + Decimal("90"),
            "consignee": party("Somebody Else Entirely"),
        }
    )
    scores = by_path(assess_fields(misread, document.text_layer, [call()], [], []))
    assert scores["total_amount"].status == "needs_review"
    assert scores["consignee.name"].status == "needs_review"
    assert scores["invoice_number"].status == "ok"


def test_line_helper_is_used_for_extra_lines() -> None:
    invoice = make_invoice(line_items=[line("Radial car tyres", "200.00")])
    assert len(assess(invoice, page_for(invoice))) == 7 + 12 + 6
