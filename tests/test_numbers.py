"""Number strings: normalise only when the format is certain; never guess."""

import pytest

from app.pipeline.numbers import normalise_numbers, read_number
from app.schemas.compact import CompactInvoice
from app.schemas.raw import RawInvoice

NO_BREAK_SPACE = chr(0xA0)


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("1234.50", "1234.50"),
        ("758", "758"),
        ("207,732.88", "207732.88"),  # comma thousands, dot decimal
        ("1.234,50", "1234.50"),  # dot thousands, comma decimal
        ("1,234,567", "1234567"),  # repeated separator groups thousands
        ("1.234.567", "1234567"),
        ("12,5", "12.5"),  # one separator, not 3 digits after: decimal
        ("12,50", "12.50"),
        ("0,125", "0.125"),  # thousands never start with 0
        ("1234,567", "1234.567"),  # more than 3 digits before: can't be grouping
        ("USD 1,234.50", "1234.50"),
        ("$ 46.00", "46.00"),
        ("€1.234,50", "1234.50"),
        ("Rs. 2,000.00", "2000.00"),  # regression: the abbreviation dot isn't a decimal
        ("Rs.2,000.00", "2000.00"),
        ("2,000.00 USD", "2000.00"),
        ("1 234.50", "1234.50"),
        ("1" + NO_BREAK_SPACE + "234,50", "1234.50"),  # no-break space
        ("1'234.50", "1234.50"),
        ("-12.50", "-12.50"),
    ],
)
def test_certain_formats_are_normalised(text: str, value: str) -> None:
    assert read_number(text).certain == value


@pytest.mark.parametrize("text", ["1,234", "1.234", "12,345", "999.000"])
def test_one_separator_with_three_digits_after_is_ambiguous(text: str) -> None:
    reading = read_number(text)
    assert reading.certain is None
    assert reading.ambiguous


def test_ambiguous_reading_offers_both_interpretations() -> None:
    reading = read_number("1,234")
    assert reading.if_dot == "1234"  # "," groups thousands
    assert reading.if_comma == "1.234"  # "," is the decimal mark


@pytest.mark.parametrize("text", ["abc", "", "12..5", "1,23,4.5", "--3", "1.234.56,7.8"])
def test_non_numbers_and_malformed_grouping_are_left_for_validation(text: str) -> None:
    reading = read_number(text)
    assert reading.certain is None
    assert not reading.ambiguous


def compact(**changes: object) -> dict:
    data: dict = {
        "invoice_number": "INV-1",
        "invoice_date": "2026-01-02",
        "shipper": {"name": "A", "address": "B", "country": "PK"},
        "buyer": {"name": "A", "address": "B", "country": "PK"},
        "consignee": {"name": "A", "address": "B", "country": "PK"},
        "notify_party": None,
        "incoterm": "CIF",
        "currency": "EUR",
        "line_items": [["Tyres", "1,000", "PCS", "12.50", "12,500.00", None]],
        "total_amount": "12,500.00",
        "gross_weight_kg": None,
        "net_weight_kg": None,
    }
    return data | changes


def test_document_convention_settles_an_ambiguous_quantity() -> None:
    data, report = normalise_numbers(compact(), CompactInvoice)
    assert data["line_items"][0][1] == "1000"  # the amounts prove "," groups thousands
    assert report.ambiguous == []
    assert "line_items[0].quantity" in report.normalised
    CompactInvoice.model_validate(data)


def test_comma_decimal_document_settles_the_other_way() -> None:
    lines = [["Tyres", "1.000", "PCS", "12,50", "12.500,00", None]]
    data, report = normalise_numbers(
        compact(line_items=lines, total_amount="12.500,00"), CompactInvoice
    )
    assert data["line_items"][0][1:5] == ["1000", "PCS", "12.50", "12500.00"]
    assert data["total_amount"] == "12500.00"
    assert report.ambiguous == []


def test_ambiguous_number_with_no_evidence_is_reported_not_guessed() -> None:
    lines = [["Tyres", "1,000", "PCS", "12", "12000", None]]
    data, report = normalise_numbers(
        compact(line_items=lines, total_amount="12000"), CompactInvoice
    )
    assert report.ambiguous == ["line_items[0].quantity"]
    assert data["line_items"][0][1] == "1,000"  # unchanged


def test_conflicting_conventions_leave_it_ambiguous() -> None:
    lines = [["Tyres", "1,000", "PCS", "12.50", "12,5", None]]
    _, report = normalise_numbers(compact(line_items=lines, total_amount="7"), CompactInvoice)
    assert report.ambiguous == ["line_items[0].quantity"]


def test_currency_alone_does_not_decide() -> None:
    lines = [["Tyres", "1,000", "PCS", "12", "12000", None]]
    _, report = normalise_numbers(
        compact(line_items=lines, total_amount="12000", currency="EUR"), CompactInvoice
    )
    assert report.ambiguous == ["line_items[0].quantity"]


def test_plain_numbers_are_untouched_and_not_reported() -> None:
    lines = [["Tyres", "1000", "PCS", "12.50", "12500.00", None]]
    data, report = normalise_numbers(
        compact(line_items=lines, total_amount="12500.00"), CompactInvoice
    )
    assert report.normalised == report.ambiguous == []
    assert data == compact(line_items=lines, total_amount="12500.00")


def test_input_is_never_modified() -> None:
    original = compact()
    snapshot = repr(original)
    normalise_numbers(original, CompactInvoice)
    assert repr(original) == snapshot


def test_text_fields_are_never_touched() -> None:
    data, _ = normalise_numbers(compact(invoice_number="1,234"), CompactInvoice)
    assert data["invoice_number"] == "1,234"


def test_raw_format_lines_are_normalised_by_name() -> None:
    line = {
        "description": "Tyres",
        "quantity": "2",
        "unit": "PCS",
        "unit_price": "1,234.50",
        "amount": "2,469.00",
        "hs_code_on_doc": None,
    }
    data, report = normalise_numbers(compact(line_items=[line]), RawInvoice)
    assert data["line_items"][0]["unit_price"] == "1234.50"
    assert "line_items[0].amount" in report.normalised


def test_json_numbers_and_nulls_are_left_alone() -> None:
    from decimal import Decimal

    lines = [["Tyres", Decimal("2"), "PCS", None, "21.00", None]]
    data, report = normalise_numbers(
        compact(line_items=lines, total_amount="21.00"), CompactInvoice
    )
    assert data["line_items"][0][1] == Decimal("2")
    assert report.normalised == []
