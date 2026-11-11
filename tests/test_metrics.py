from datetime import date
from decimal import Decimal

import pytest

from app.schemas.raw import RawInvoice, RawLineItem, RawParty
from evals.metrics import (
    CRITICAL_HEADER_FIELDS,
    CRITICAL_LINE_NAME,
    HEADER_FIELDS,
    LINE_FIELDS,
    PARTIES,
    PARTY_FIELDS,
    DocumentScore,
    FieldScore,
    compare,
    match_lines,
    normalise_text,
    ratio,
    score_document,
    similarity,
    summarise,
)


def line(description: str, amount: str = "100.00", **changes: object) -> RawLineItem:
    values: dict[str, object] = {
        "description": description,
        "quantity": Decimal("2"),
        "unit": "PCS",
        "unit_price": Decimal(amount) / 2,
        "amount": Decimal(amount),
        "hs_code_on_doc": None,
    }
    return RawLineItem(**(values | changes))


def party(name: str, country: str = "PK") -> RawParty:
    return RawParty(name=name, address=f"Plot 1, Block A, Karachi {len(name)}", country=country)


def make_invoice(**changes: object) -> RawInvoice:
    values: dict[str, object] = {
        "invoice_number": "INV-2026-00001",
        "invoice_date": date(2026, 3, 4),
        "shipper": party("Lucas Delmas S.A.R.L.", "FR"),
        "buyer": party("Tawkeel, Abaan and Gulshan"),
        "consignee": party("Tawkeel, Abaan and Gulshan"),
        "notify_party": None,
        "incoterm": "CIF",
        "currency": "USD",
        "line_items": [line("Radial car tyres", "200.00"), line("LED panel lights", "50.50")],
        "total_amount": Decimal("250.50"),
        "gross_weight_kg": Decimal("110.00"),
        "net_weight_kg": Decimal("100.00"),
    }
    return RawInvoice(**(values | changes))


def outcomes(score: DocumentScore) -> dict[str, str]:
    """Field name to outcome, for documents where each field name appears once."""
    return {field.field: field.outcome for field in score.fields}


def wrong_fields(score: DocumentScore) -> list[str]:
    return [field.field for field in score.fields if field.outcome != "correct"]


# --- single values -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expected", "predicted", "outcome"),
    [
        (None, None, "correct"),
        ("A", None, "missed"),
        (None, "A", "hallucinated"),
        ("A", "A", "correct"),
        ("A", "B", "wrong"),
    ],
)
def test_missing_value_rules(expected: str | None, predicted: str | None, outcome: str) -> None:
    assert compare("exact", expected, predicted) == outcome


def test_an_empty_string_is_a_value_not_a_missing_one() -> None:
    assert compare("exact", None, "") == "hallucinated"
    assert compare("exact", "A", "") == "wrong"


@pytest.mark.parametrize(
    ("expected", "predicted", "outcome"),
    [
        ("46", "46.00", "correct"),
        ("46.00", "46", "correct"),
        ("1234.50", "1234.5", "correct"),
        ("46.00", "46.01", "wrong"),
        ("46.00", "45.99", "wrong"),
        ("0.10", "0.1000", "correct"),
        ("1000", "100", "wrong"),
    ],
)
def test_numbers_match_to_two_decimal_places(expected: str, predicted: str, outcome: str) -> None:
    assert compare("number", Decimal(expected), Decimal(predicted)) == outcome


def test_exact_fields_keep_case_but_ignore_surrounding_spaces() -> None:
    assert compare("exact", "INV-2026-1", " INV-2026-1 ") == "correct"
    assert compare("exact", "INV-2026-1", "inv-2026-1") == "wrong"
    assert compare("exact", "INV-2026-1", "INV 2026-1") == "wrong"


def test_codes_ignore_case() -> None:
    assert compare("code", "PK", "pk") == "correct"
    assert compare("code", "USD", "US$") == "wrong"


def test_dates_must_be_equal() -> None:
    assert compare("date", date(2026, 3, 4), date(2026, 3, 4)) == "correct"
    assert compare("date", date(2026, 3, 4), date(2026, 4, 3)) == "wrong"


def test_fuzzy_ignores_case_punctuation_and_spacing() -> None:
    expected = "Plot 321, Block C, Faisalabad 82097"
    assert compare("fuzzy", expected, "PLOT 321 BLOCK C  FAISALABAD 82097") == "correct"


def test_fuzzy_threshold_is_ninety_percent() -> None:
    assert similarity("abcdefghij", "abcdefghiX") == pytest.approx(0.9)
    assert compare("fuzzy", "abcdefghij", "abcdefghiX") == "correct"
    assert compare("fuzzy", "abcdefghij", "abcdefghXX") == "wrong"


def test_fuzzy_rejects_a_different_company() -> None:
    assert compare("fuzzy", "Tawkeel, Abaan and Gulshan", "Lucas Delmas S.A.R.L.") == "wrong"


def test_unicode_names_compare_by_meaning_not_encoding() -> None:
    composed = "İçtaş İnşaat"  # İçtaş İnşaat, precomposed
    decomposed = "İçtaş İnşaat"  # same text, combining marks
    assert normalise_text(composed) == normalise_text(decomposed)
    assert compare("fuzzy", composed, decomposed) == "correct"
    assert compare("fuzzy", "Müller GmbH", "MÜLLER GMBH") == "correct"


def test_ratio_is_none_without_a_denominator() -> None:
    assert ratio(0, 0) is None
    assert ratio(1, 4) == 0.25


# --- whole documents -----------------------------------------------------------------------


def test_identical_documents_score_every_field_correct() -> None:
    invoice = make_invoice()
    score = score_document(invoice, invoice)
    assert wrong_fields(score) == []
    assert score.all_critical_correct
    assert score.lines.model_dump() == {"gold": 2, "predicted": 2, "matched": 2}


def test_every_field_is_scored_once_per_document() -> None:
    score = score_document(make_invoice(), make_invoice())
    expected = [*HEADER_FIELDS]
    expected += [f"{p}.{f}" for p in PARTIES for f in PARTY_FIELDS]
    expected += [f"line.{f}" for f in LINE_FIELDS] * 2
    assert sorted(field.field for field in score.fields) == sorted(expected)


def test_one_wrong_field_is_the_only_wrong_field() -> None:
    predicted = make_invoice(currency="EUR")
    score = score_document(make_invoice(), predicted)
    assert wrong_fields(score) == ["currency"]
    assert not score.all_critical_correct


def test_a_non_critical_error_keeps_the_document_critical_correct() -> None:
    score = score_document(make_invoice(), make_invoice(net_weight_kg=Decimal("99.00")))
    assert wrong_fields(score) == ["net_weight_kg"]
    assert score.all_critical_correct


def test_critical_fields_are_the_designed_set() -> None:
    assert CRITICAL_HEADER_FIELDS == ("invoice_number", "total_amount", "currency", "incoterm")
    score = score_document(make_invoice(), make_invoice())
    names = [field.field for field in score.critical]
    assert names == [*CRITICAL_HEADER_FIELDS, CRITICAL_LINE_NAME, CRITICAL_LINE_NAME]


def test_missing_header_value_is_a_miss() -> None:
    score = score_document(make_invoice(), make_invoice(invoice_number=None))
    assert outcomes(score)["invoice_number"] == "missed"


def test_absent_notify_party_on_both_sides_is_correct() -> None:
    score = score_document(make_invoice(), make_invoice())
    for field in PARTY_FIELDS:
        assert outcomes(score)[f"notify_party.{field}"] == "correct"


def test_invented_notify_party_is_a_hallucination() -> None:
    score = score_document(make_invoice(), make_invoice(notify_party=party("Clearing Agent")))
    for field in PARTY_FIELDS:
        assert outcomes(score)[f"notify_party.{field}"] == "hallucinated"


def test_dropped_notify_party_is_a_miss() -> None:
    truth = make_invoice(notify_party=party("Clearing Agent"))
    score = score_document(truth, make_invoice())
    for field in PARTY_FIELDS:
        assert outcomes(score)[f"notify_party.{field}"] == "missed"


def test_buyer_copied_from_consignee_is_wrong_when_they_differ() -> None:
    truth = make_invoice(buyer=party("Habib Traders"))
    copied = make_invoice()  # buyer == consignee
    score = score_document(truth, copied)
    assert outcomes(score)["buyer.name"] == "wrong"
    assert outcomes(score)["consignee.name"] == "correct"


def test_buyer_copied_from_consignee_is_right_when_they_are_the_same() -> None:
    score = score_document(make_invoice(), make_invoice())
    assert outcomes(score)["buyer.name"] == "correct"


def test_no_output_scores_every_field_as_no_output() -> None:
    score = score_document(make_invoice(), None)
    assert {field.outcome for field in score.fields} == {"no_output"}
    assert {field.outcome for field in score.critical} == {"no_output"}
    assert len(score.critical) == len(CRITICAL_HEADER_FIELDS) + 2
    assert score.lines.model_dump() == {"gold": 2, "predicted": 0, "matched": 0}
    assert not score.all_critical_correct


def test_no_output_never_counts_an_absent_notify_party_as_correct() -> None:
    score = score_document(make_invoice(), None)
    assert outcomes(score)["notify_party.name"] == "no_output"


# --- line items ----------------------------------------------------------------------------


def test_reordered_lines_still_match() -> None:
    truth = make_invoice()
    reordered = make_invoice(line_items=list(reversed(truth.line_items)))
    score = score_document(truth, reordered)
    assert wrong_fields(score) == []
    assert score.all_critical_correct


def test_dropped_line_lowers_recall_and_fails_critical() -> None:
    truth = make_invoice()
    score = score_document(truth, make_invoice(line_items=truth.line_items[:1]))
    assert score.lines.model_dump() == {"gold": 2, "predicted": 1, "matched": 1}
    assert [f.outcome for f in score.critical if f.field == CRITICAL_LINE_NAME] == [
        "correct",
        "missed",
    ]
    assert not score.all_critical_correct
    # Per-field line accuracy covers matched lines only.
    assert wrong_fields(score) == []


def test_extra_line_lowers_precision_and_is_a_hallucination() -> None:
    truth = make_invoice()
    extra = make_invoice(line_items=[*truth.line_items, line("Freight charges", "30.00")])
    score = score_document(truth, extra)
    assert score.lines.model_dump() == {"gold": 2, "predicted": 3, "matched": 2}
    line_outcomes = [f.outcome for f in score.critical if f.field == CRITICAL_LINE_NAME]
    assert line_outcomes == ["correct", "correct", "hallucinated"]
    assert not score.all_critical_correct


def test_wrong_line_amount_is_wrong_on_that_line_only() -> None:
    truth = make_invoice()
    changed = [truth.line_items[0], truth.line_items[1].model_copy(update={"amount": Decimal("5")})]
    score = score_document(truth, make_invoice(line_items=changed))
    assert wrong_fields(score) == ["line.amount"]
    assert not score.all_critical_correct


def test_unrelated_descriptions_are_not_paired() -> None:
    gold = [line("Radial car tyres")]
    predicted = [line("Hot-rolled steel coils")]
    assert match_lines(gold, predicted) == []


def test_small_description_typo_still_pairs_the_line() -> None:
    gold = [line("Radial car tyres 205/55 R16")]
    predicted = [line("Radial car tyre 205/55 R1G")]
    assert match_lines(gold, predicted) == [(0, 0)]


def test_duplicate_descriptions_pair_by_their_other_fields() -> None:
    gold = [line("Cotton bed linen", "100.00"), line("Cotton bed linen", "300.00")]
    predicted = [line("Cotton bed linen", "300.00"), line("Cotton bed linen", "100.00")]
    assert sorted(match_lines(gold, predicted)) == [(0, 1), (1, 0)]
    score = score_document(make_invoice(line_items=gold), make_invoice(line_items=predicted))
    assert wrong_fields(score) == []


def test_empty_line_items_on_both_sides() -> None:
    truth = make_invoice(line_items=[])
    score = score_document(truth, truth)
    assert score.lines.model_dump() == {"gold": 0, "predicted": 0, "matched": 0}
    assert score.all_critical_correct


def test_lines_predicted_where_the_truth_has_none_are_hallucinated() -> None:
    score = score_document(make_invoice(line_items=[]), make_invoice())
    line_outcomes = [f.outcome for f in score.critical if f.field == CRITICAL_LINE_NAME]
    assert line_outcomes == ["hallucinated", "hallucinated"]


def test_no_lines_predicted_where_the_truth_has_some_are_missed() -> None:
    score = score_document(make_invoice(), make_invoice(line_items=[]))
    line_outcomes = [f.outcome for f in score.critical if f.field == CRITICAL_LINE_NAME]
    assert line_outcomes == ["missed", "missed"]


def test_line_with_missing_description_is_never_paired() -> None:
    assert (
        match_lines(
            [line("Radial car tyres")], [line("x").model_copy(update={"description": None})]
        )
        == []
    )


def test_hs_code_printed_but_not_extracted_is_a_miss() -> None:
    truth = make_invoice(line_items=[line("Radial car tyres", hs_code_on_doc="4011.1000")])
    score = score_document(truth, make_invoice(line_items=[line("Radial car tyres")]))
    assert outcomes(score)["line.hs_code_on_doc"] == "missed"


# --- aggregation ---------------------------------------------------------------------------


def test_summary_of_perfect_documents_is_all_ones() -> None:
    invoice = make_invoice()
    summary = summarise([score_document(invoice, invoice)] * 3)
    assert summary.documents == 3
    assert {field.accuracy for field in summary.fields} == {1.0}
    assert summary.line_precision == summary.line_recall == summary.line_f1 == 1.0
    assert summary.critical_field_accuracy == 1.0
    assert summary.all_critical_correct_rate == 1.0


def test_summary_counts_outcomes_per_field() -> None:
    truth = make_invoice()
    scores = [
        score_document(truth, truth),
        score_document(truth, make_invoice(currency="EUR")),
        score_document(truth, make_invoice(currency=None)),
        score_document(truth, None),
    ]
    currency = next(field for field in summarise(scores).fields if field.field == "currency")
    assert currency.model_dump() == {
        "field": "currency",
        "total": 4,
        "correct": 1,
        "wrong": 1,
        "missed": 1,
        "hallucinated": 0,
        "no_output": 1,
        "accuracy": 0.25,
    }


def test_summary_micro_averages_lines_across_documents() -> None:
    truth = make_invoice()
    one_line = make_invoice(line_items=truth.line_items[:1])
    summary = summarise([score_document(truth, truth), score_document(truth, one_line)])
    assert summary.line_precision == 1.0
    assert summary.line_recall == pytest.approx(3 / 4)
    assert summary.line_f1 == pytest.approx(2 * 1.0 * 0.75 / 1.75)
    # 2 docs x 4 header fields + 4 gold lines; one line missed.
    assert summary.critical_field_accuracy == pytest.approx(11 / 12)
    assert summary.all_critical_correct_rate == 0.5


def test_summary_of_no_documents_reports_none_not_zero() -> None:
    summary = summarise([])
    assert summary.documents == 0
    assert summary.fields == []
    assert summary.line_precision is None
    assert summary.line_f1 is None
    assert summary.critical_field_accuracy is None
    assert summary.all_critical_correct_rate is None


def test_f1_is_zero_when_nothing_matches() -> None:
    truth = make_invoice(line_items=[line("Radial car tyres")])
    predicted = make_invoice(line_items=[line("Hot-rolled steel coils")])
    summary = summarise([score_document(truth, predicted)])
    assert summary.line_precision == summary.line_recall == summary.line_f1 == 0.0


def test_summary_orders_fields_header_then_parties_then_lines() -> None:
    invoice = make_invoice()
    names = [field.field for field in summarise([score_document(invoice, invoice)]).fields]
    assert names[0] == "invoice_number"
    assert names.index("shipper.name") < names.index("line.description")
    assert names[-1] == "line.hs_code_on_doc"


def test_field_score_keeps_text_for_reports() -> None:
    score = score_document(make_invoice(), make_invoice(total_amount=Decimal("1.00")))
    total = next(field for field in score.fields if field.field == "total_amount")
    assert total == FieldScore(
        field="total_amount", outcome="wrong", expected="250.50", predicted="1.00"
    )
