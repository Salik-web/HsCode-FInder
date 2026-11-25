"""Confidence scoring (app/pipeline/confidence.py): every signal, the threshold, the reasons."""

import random
from datetime import date
from decimal import Decimal

import pytest

from app.pipeline.confidence import (
    ADDRESSES_REVIEWED_ON_SCANS,
    CRITICAL_FIELD_EMPTY,
    LINE_FIELDS,
    NO_TEXT_LAYER_TO_VERIFY,
    REVIEW_THRESHOLD,
    ROW_UNDER_REVIEW,
    PageText,
    assess_fields,
    date_renderings,
    field_values,
    is_critical,
    normalise,
    numbers_in,
    row_coverage,
    row_of,
)
from app.pipeline.preprocess import prepare_document
from app.pipeline.rules import REFERENCE_RULE, describe_change, parse_change
from app.schemas.raw import RawInvoice, RawLineItem
from app.schemas.result import FieldAssessment, FieldCheck, LLMCall
from synth.generator import derive_rng, generate_ground_truth, load_catalogue
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
        parts.append(
            f"{item.description} {item.unit} {item.unit_price:,.2f} {item.quantity} "
            f"{item.amount:,.2f}"
        )
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


# --- rows ----------------------------------------------------------------------------------

# Two rows of diag_00007 as PDFium reads the ledger layout: description, HS, unit, price, qty.
PAPER = line(
    "Printing paper A4 80 gsm",
    "75.26",
    quantity=Decimal("2"),
    unit="CTN",
    unit_price=Decimal("37.63"),
)
COILS = line(
    "Hot-rolled steel coils",
    "339.86",
    quantity=Decimal("1.47"),
    unit="MT",
    unit_price=Decimal("231.20"),
)
TWO_ROWS = (
    "Printing paper A4 80 gsm 4802.5600 CTN 37.63 2 75.26\n"
    "Hot-rolled steel coils 7208.3700 MT 231.20 1.47 339.86"
)


def assess_lines(text: str, *items: RawLineItem) -> dict[str, FieldAssessment]:
    return assess(make_invoice(line_items=list(items)), text)


def test_a_unit_printed_only_in_another_row_goes_to_review() -> None:
    """diag_00007: the model read MT as CTN, which the page prints, but on another row."""
    assert PageText(TWO_ROWS).agreement("line_items[1].unit", "CTN") == "found"
    scores = assess_lines(TWO_ROWS, PAPER, COILS.model_copy(update={"unit": "CTN"}))
    assert scores["line_items[1].unit"].status == "needs_review"
    assert scores["line_items[1].unit"].reasons[-1] == "not found in text layer -0.45"
    for field in ("description", "quantity", "unit_price", "amount"):
        assert scores[f"line_items[1].{field}"].status == "ok", field
    assert scores["line_items[0].unit"].status == "ok"


@pytest.mark.parametrize(
    ("field", "value"),
    [("quantity", Decimal("2")), ("unit_price", Decimal("37.63")), ("amount", Decimal("75.26"))],
)
def test_a_number_printed_only_in_another_row_goes_to_review(field: str, value: Decimal) -> None:
    scores = assess_lines(TWO_ROWS, PAPER, COILS.model_copy(update={field: value}))
    assert scores[f"line_items[1].{field}"].status == "needs_review"


def test_a_garbled_description_still_finds_its_row() -> None:
    """diag_00007's actual answer: 'Hot-rolled钢 coils' with unit CTN."""
    garbled = COILS.model_copy(update={"description": "Hot-rolled钢 coils", "unit": "CTN"})
    scores = assess_lines(TWO_ROWS, PAPER, garbled)
    assert scores["line_items[1].description"].status == "needs_review"
    assert scores["line_items[1].unit"].status == "needs_review"
    for field in ("quantity", "unit_price", "amount"):
        assert scores[f"line_items[1].{field}"].status == "ok", field


def test_a_line_whose_description_has_no_row_goes_to_review() -> None:
    stranger = COILS.model_copy(update={"description": "Frozen boneless beef"})
    scores = assess_lines(TWO_ROWS, PAPER, stranger)
    for field in ("quantity", "unit", "unit_price", "amount"):
        assert scores[f"line_items[1].{field}"].status == "needs_review", field


def test_a_line_without_a_description_has_no_row() -> None:
    scores = assess_lines(TWO_ROWS, PAPER, COILS.model_copy(update={"description": None}))
    # None isn't looked up; the line goes to review because its amount has no row
    assert scores["line_items[1].description"].reasons == [
        "text layer: base 0.75",
        ROW_UNDER_REVIEW,
    ]
    assert scores["line_items[1].amount"].status == "needs_review"
    assert scores["line_items[1].unit"].status == "needs_review"


def test_a_wrapped_description_takes_the_next_text_line_as_its_row() -> None:
    """Sidebar wraps a long description; the row's remaining cells follow the last part."""
    text = (
        "4 233.51 Cotton yarn, combed, 30s\n"
        "count\n"
        "KG 5205.2400 460.59 107,552.37\n"
        "5 686.83 Copper winding wire PCS 8544.1100 493.31 338,820.11"
    )
    yarn = line(
        "Cotton yarn, combed, 30s count",
        "107552.37",
        quantity=Decimal("233.51"),
        unit="KG",
        unit_price=Decimal("460.59"),
    )
    scores = assess_lines(
        text, yarn, yarn.model_copy(update={"description": "Copper winding wire"})
    )
    for field in ("quantity", "unit", "unit_price", "amount"):
        assert scores[f"line_items[0].{field}"].status == "ok", field
    assert scores["line_items[1].unit"].status == "needs_review"  # KG isn't in Copper's row


def test_a_wrapped_row_never_takes_the_next_description_line() -> None:
    page = PageText("Cotton yarn, combed, 30s\ncount\nCopper winding wire KG 8544.1100")
    rows = page.item_rows(["Cotton yarn, combed, 30s count", "Copper winding wire"])
    assert not rows[0][0].contains("KG")
    assert rows[1][0].contains("KG")


def test_two_rows_with_the_same_goods_both_count() -> None:
    text = "Smartphones 5G PCS 10\nLaptop computers PCS 3\nSmartphones 5G SET 7"
    page = PageText(text)
    assert page.description_spans("Smartphones 5G") == [(0, 1), (2, 3)]
    assert page.description_spans("Laptop computers") == [(1, 2)]


def test_row_coverage_counts_only_runs_of_characters() -> None:
    assert row_coverage("hot rolled coils", " hot rolled coils 7208 ") == 1.0
    assert row_coverage("abcd", " a b c d ") == 0.0
    assert row_coverage("", " anything ") == 0.0


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


def test_without_a_text_layer_critical_fields_always_go_to_review() -> None:
    """Photos: nothing can verify a value, so every critical field goes to a person."""
    scores = assess(make_invoice(), None)
    critical = [path for path in scores if is_critical(path)]
    assert sorted(critical) == sorted(
        [
            "invoice_number",
            "total_amount",
            "currency",
            "incoterm",
            "buyer.name",
            "consignee.name",
            "line_items[0].amount",
            "line_items[1].amount",
        ]
    )
    for path, assessment in scores.items():
        if path in critical:
            assert assessment.status == "needs_review", path
            assert assessment.reasons[-1] == NO_TEXT_LAYER_TO_VERIFY
            assert assessment.confidence == 0.60  # the evidence is unchanged
        elif path.startswith("line_items["):
            # every amount is in review on a photo, so every row is (Salik, 2026-11-17)
            assert assessment.status == "needs_review", path
            assert assessment.reasons[-1] == ROW_UNDER_REVIEW
        elif path in ADDRESSES_REVIEWED_ON_SCANS:
            assert assessment.status == "needs_review", path
            assert assessment.reasons[-1] == NO_TEXT_LAYER_TO_VERIFY
        else:
            assert assessment.status == "ok", path


# --- whole rows (Salik, 2026-11-17) ----------------------------------------------------------

LINE_ONE = [f"line_items[1].{field}" for field in LINE_FIELDS]


def statuses(scores: dict[str, FieldAssessment], paths: list[str]) -> list[str]:
    return [scores[path].status for path in paths]


def test_a_failed_line_check_sends_the_whole_row_to_review() -> None:
    """inv_00034_messy: the line check flagged the amount, but the misread was the price."""
    invoice = make_invoice()
    check = FieldCheck(path="line_items[1].amount", status="failed_check", rule="r", detail="d")
    scores = assess(invoice, page_for(invoice), checks=[check])
    for path in LINE_ONE:
        if path == "line_items[1].amount":
            assert scores[path].status == "failed_check"
            continue
        assert scores[path].status == "needs_review", path
        assert scores[path].reasons[-1] == ROW_UNDER_REVIEW
        assert scores[path].confidence >= REVIEW_THRESHOLD  # the score is left as it was
    assert set(statuses(scores, [f"line_items[0].{f}" for f in LINE_FIELDS])) == {"ok"}
    assert scores["invoice_number"].status == "ok"


def test_an_amount_not_on_the_page_sends_the_whole_row_to_review() -> None:
    invoice = make_invoice()
    shifted = invoice.line_items[1].model_copy(update={"amount": Decimal("98762.84")})
    answer = invoice.model_copy(update={"line_items": [invoice.line_items[0], shifted]})
    scores = assess(answer, page_for(invoice))
    assert set(statuses(scores, LINE_ONE)) == {"needs_review"}
    assert scores["line_items[1].quantity"].reasons[-1] == ROW_UNDER_REVIEW


def test_an_empty_amount_sends_the_whole_row_to_review() -> None:
    invoice = make_invoice()
    scores = assess(emptied(invoice), page_for(invoice))
    assert set(statuses(scores, [f"line_items[0].{f}" for f in LINE_FIELDS])) == {"needs_review"}
    assert set(statuses(scores, LINE_ONE)) == {"ok"}  # its amount is there


def test_a_non_critical_field_in_review_leaves_the_rest_of_its_row_alone() -> None:
    invoice = make_invoice()
    misread = invoice.line_items[1].model_copy(update={"unit": "CTN"})
    scores = assess(
        invoice.model_copy(update={"line_items": [invoice.line_items[0], misread]}),
        page_for(invoice),
    )
    assert scores["line_items[1].unit"].status == "needs_review"
    assert scores["line_items[1].amount"].status == "ok"
    assert scores["line_items[1].quantity"].status == "ok"


def test_row_of() -> None:
    assert row_of("line_items[12].unit_price") == "line_items[12]"
    assert row_of("buyer.name") is None
    assert row_of("total_amount") is None


EMPTY_CRITICAL = (
    "invoice_number",
    "total_amount",
    "currency",
    "incoterm",
    "buyer.name",
    "consignee.name",
    "line_items[0].amount",
)


def emptied(invoice: RawInvoice) -> RawInvoice:
    """Every critical field set to None, as an answer that found none of them."""
    first, *rest = invoice.line_items
    return invoice.model_copy(
        update={
            "invoice_number": None,
            "total_amount": None,
            "currency": None,
            "incoterm": None,
            "buyer": invoice.buyer.model_copy(update={"name": None}),
            "consignee": invoice.consignee.model_copy(update={"name": None}),
            "line_items": [first.model_copy(update={"amount": None}), *rest],
        }
    )


@pytest.mark.parametrize("path", EMPTY_CRITICAL)
def test_an_empty_critical_field_goes_to_review_with_a_text_layer(path: str) -> None:
    """With a text layer, None scored the base 0.75 and stayed ok (a dropped line amount)."""
    invoice = make_invoice()
    scores = assess(emptied(invoice), page_for(invoice))
    assert scores[path].status == "needs_review"
    assert scores[path].reasons == ["text layer: base 0.75", CRITICAL_FIELD_EMPTY]
    assert scores[path].confidence == 0.75  # the status is a policy; the score is unchanged


@pytest.mark.parametrize("path", EMPTY_CRITICAL)
def test_an_empty_critical_field_on_a_photo_says_it_is_empty(path: str) -> None:
    scores = assess(emptied(make_invoice()), None)
    assert scores[path].status == "needs_review"
    assert scores[path].reasons[-1] == CRITICAL_FIELD_EMPTY


def test_empty_non_critical_fields_stay_ok() -> None:
    invoice = make_invoice()
    blank = invoice.model_copy(
        update={
            "gross_weight_kg": None,
            "invoice_date": None,
            "notify_party": None,
            "shipper": invoice.shipper.model_copy(update={"name": None}),
            "buyer": invoice.buyer.model_copy(update={"address": None}),
        }
    )
    scores = assess(blank, page_for(invoice))
    for path in (
        "gross_weight_kg",
        "invoice_date",
        "notify_party.name",
        "shipper.name",
        "buyer.address",
        "line_items[0].hs_code_on_doc",
    ):
        assert scores[path].status == "ok", path


def test_an_empty_critical_field_never_hides_a_failed_check() -> None:
    invoice = make_invoice()
    check = FieldCheck(path="line_items[0].amount", status="failed_check", rule="r", detail="d")
    scores = assess(emptied(invoice), page_for(invoice), checks=[check])
    assert scores["line_items[0].amount"].status == "failed_check"


def test_on_a_scan_the_three_main_addresses_go_to_review_and_nothing_else_is_added() -> None:
    """Regression: a misread place name in a scanned address passed as ok."""
    invoice = make_invoice()
    scores = assess(invoice, None)
    for name in ("shipper", "buyer", "consignee"):
        assert scores[f"{name}.address"].status == "needs_review"
        assert scores[f"{name}.address"].confidence == 0.60  # the evidence is unchanged
        assert scores[f"{name}.country"].status == "ok"
    for path in ("shipper.name", "notify_party.address", "invoice_date", "net_weight_kg"):
        assert scores[path].status == "ok"


def test_with_a_text_layer_addresses_are_scored_as_usual() -> None:
    invoice = make_invoice()
    scores = assess(invoice, page_for(invoice))
    for path in ADDRESSES_REVIEWED_ON_SCANS:
        assert scores[path].status == "ok"


def test_with_a_text_layer_critical_fields_are_scored_as_usual() -> None:
    invoice = make_invoice()
    scores = assess(invoice, page_for(invoice))
    assert scores["total_amount"].status == "ok"
    assert NO_TEXT_LAYER_TO_VERIFY not in scores["total_amount"].reasons


def test_a_failed_check_on_a_photo_stays_failed_check() -> None:
    check = FieldCheck(path="currency", status="failed_check", rule="iso_currency", detail="x")
    currency = assess(make_invoice(), None, checks=[check])["currency"]
    assert currency.status == "failed_check"
    assert NO_TEXT_LAYER_TO_VERIFY not in currency.reasons


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
    # Non-critical fields, so the photo rule doesn't decide the status.
    retried = [call(1, validation_errors=["x"]), call(2)]
    assert assess(make_invoice(), None, retried)["gross_weight_kg"].status == "ok"
    repaired = [call(1, validation_errors=["x"]), call(2, keys_ignored=["notes"])]
    weight = assess(make_invoice(), None, repaired)["gross_weight_kg"]
    assert (weight.confidence, weight.status) == (0.45, "needs_review")


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


def test_diag_00007_unit_copied_from_another_row_is_caught_on_the_rendered_page() -> None:
    """Regression: diag_00007 (seed 1116, ledger), as rendered, with the model's v3 answer.

    The page prints CTN on another row, so the page-wide search trusted it at 0.95.
    """
    truth = generate_ground_truth(1116, 7, load_catalogue())
    html = render_html(
        truth.invoice, "ledger", pick_labels(derive_rng(1116, "labels", 7)), truth.buyer_reference
    )
    with open_browser_page() as page:
        document = prepare_document(render_pdf(page, html))
    assert document.text_layer is not None
    assert PageText(document.text_layer).agreement("line_items[0].unit", "CTN") == "found"
    index, coils = next(
        (index, item)
        for index, item in enumerate(truth.invoice.line_items)
        if item.description == "Hot-rolled steel coils"
    )
    assert coils.unit == "MT"
    answer = truth.invoice.model_copy(
        update={
            "line_items": [
                item.model_copy(update={"description": "Hot-rolled钢 coils", "unit": "CTN"})
                if position == index
                else item
                for position, item in enumerate(truth.invoice.line_items)
            ]
        }
    )
    rules = [REFERENCE_RULE] if truth.buyer_reference else []
    scores = by_path(assess_fields(answer, document.text_layer, [call()], rules, []))
    assert scores[f"line_items[{index}].unit"].status == "needs_review"
    assert scores[f"line_items[{index}].description"].status == "needs_review"
    for field in ("quantity", "unit_price", "amount"):
        assert scores[f"line_items[{index}].{field}"].status == "ok", field
    flagged = [path for path, score in scores.items() if score.status != "ok"]
    assert flagged == [f"line_items[{index}].description", f"line_items[{index}].unit"]


def test_line_helper_is_used_for_extra_lines() -> None:
    invoice = make_invoice(line_items=[line("Radial car tyres", "200.00")])
    assert len(assess(invoice, page_for(invoice))) == 7 + 12 + 6
