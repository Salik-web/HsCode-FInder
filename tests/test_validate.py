"""Guardrails (app/pipeline/validate.py): every check, its tolerance edges and its skips."""

import random
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.pipeline.preprocess import prepare_document
from app.pipeline.rules import UNIT_RULE
from app.pipeline.validate import (
    COUNTRY_RULE,
    CURRENCY_RULE,
    DATE_RULE,
    INCOTERM_RULE,
    LINE_MATH_RULE,
    PARTY_BOUNDARY_RULE,
    TOTAL_RULE,
    WEIGHT_RULE,
    check_currency,
    check_date,
    check_incoterm,
    check_line_math,
    check_party_boundaries,
    check_total,
    check_weights,
    total_tolerance,
    validate_invoice,
    verify_invoice,
    years_before,
)
from app.schemas.iso_codes import COUNTRIES, CURRENCIES
from app.schemas.raw import RawInvoice, RawLineItem
from synth.generator import (
    ALL_COUNTRIES,
    ANCHOR_DATE,
    generate_ground_truth,
    load_catalogue,
)
from synth.generator import CURRENCIES as GENERATED_CURRENCIES
from synth.layouts import TEMPLATES, pick_labels
from synth.render import open_browser_page, render_html, render_pdf
from tests.test_metrics import make_invoice, party

AS_OF = date(2026, 10, 1)


def item(quantity: str | None, unit_price: str | None, amount: str | None) -> RawLineItem:
    return RawLineItem(
        description="Radial car tyres",
        quantity=None if quantity is None else Decimal(quantity),
        unit="PCS",
        unit_price=None if unit_price is None else Decimal(unit_price),
        amount=None if amount is None else Decimal(amount),
        hs_code_on_doc=None,
    )


def with_amounts(total: str | None, *amounts: str | None) -> RawInvoice:
    lines = [item(None, None, amount) for amount in amounts]
    return make_invoice(line_items=lines, total_amount=None if total is None else Decimal(total))


def rules(checks: list) -> list[tuple[str, str]]:
    return [(check.path, check.rule) for check in checks]


# --- clean invoices ------------------------------------------------------------------------


def test_generated_ground_truth_passes_every_check() -> None:
    """The false-alarm floor: invoices that are right by construction never fail a check."""
    catalogue = load_catalogue()
    for index in range(300):
        invoice = generate_ground_truth(42, index, catalogue).invoice
        assert verify_invoice(invoice, ANCHOR_DATE).checks == [], index


def test_the_metrics_test_invoice_passes() -> None:
    assert validate_invoice(make_invoice(), AS_OF) == []


# --- line math -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("quantity", "unit_price", "amount"),
    [
        ("2", "100.00", "200.00"),  # exact
        ("3", "0.335", "1.01"),  # 1.005 rounded half up
        ("3", "0.335", "1.00"),  # 1.005 rounded half down: still within a cent
        ("1000", "0.333", "333.01"),  # off by exactly 0.01: the tolerance is inclusive
        ("36", "336.94", "12129.84"),
        ("-2", "10.00", "-20.00"),  # a credit line
        ("0", "15.00", "0.00"),
        ("1.5", "2.10", "3.15"),  # a fractional quantity (kilograms, metres)
    ],
)
def test_line_math_within_a_cent_passes(quantity: str, unit_price: str, amount: str) -> None:
    assert check_line_math(item(quantity, unit_price, amount), 0) == []


@pytest.mark.parametrize(
    ("quantity", "unit_price", "amount"),
    [
        ("1000", "0.333", "333.02"),  # off by 0.02
        ("3", "0.335", "1.02"),  # off by 0.015: just past the tolerance
        ("2", "100.00", "2000.00"),  # decimal point shifted
        ("36", "336.94", "12219.84"),  # two digits swapped
        ("2", "100.00", "-200.00"),  # sign lost
    ],
)
def test_line_math_off_by_more_than_a_cent_fails(
    quantity: str, unit_price: str, amount: str
) -> None:
    checks = check_line_math(item(quantity, unit_price, amount), 4)
    assert rules(checks) == [("line_items[4].amount", LINE_MATH_RULE)]
    assert amount in checks[0].detail


def test_line_math_off_by_a_hundredth_of_a_cent_more_fails() -> None:
    """Decimal arithmetic, not float: 0.0101 must not round down to the tolerance."""
    assert check_line_math(item("1", "10.0000", "10.0101"), 0) != []
    assert check_line_math(item("1", "10.0000", "10.0100"), 0) == []


@pytest.mark.parametrize(
    ("quantity", "unit_price", "amount"),
    [(None, "1.00", "5.00"), ("5", None, "5.00"), ("5", "1.00", None)],
)
def test_line_math_is_skipped_when_a_value_is_missing(
    quantity: str | None, unit_price: str | None, amount: str | None
) -> None:
    assert check_line_math(item(quantity, unit_price, amount), 0) == []


# --- invoice total -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line_count", "tolerance"), [(1, "0.010"), (2, "0.015"), (3, "0.020"), (15, "0.080")]
)
def test_total_tolerance_is_half_a_cent_per_rounding(line_count: int, tolerance: str) -> None:
    assert total_tolerance(line_count) == Decimal(tolerance)


@pytest.mark.parametrize(
    ("total", "amounts"),
    [
        ("300.00", ("100.00", "200.00")),
        ("100.01", ("100.00",)),  # one line: off by exactly a cent, inclusive
        ("99.99", ("100.00",)),
        ("3.02", ("1.00", "1.00", "1.00")),  # three lines allow 0.02
        ("100000.01", ("100000.00",)),  # size of the total doesn't widen the tolerance
        ("0.00", ("0.00",)),
        ("-50.00", ("-50.00",)),
    ],
)
def test_total_within_rounding_passes(total: str, amounts: tuple[str, ...]) -> None:
    assert check_total(with_amounts(total, *amounts)) == []


@pytest.mark.parametrize(
    ("total", "amounts"),
    [
        ("100.02", ("100.00",)),  # one line: just past a cent
        ("3.03", ("1.00", "1.00", "1.00")),  # three lines: just past 0.02
        ("100000.00", ("99999.98",)),
        ("210093.95", ("210093.59",)),  # swapped cents: missed by the old 0.5% tolerance
        ("579663.29", ("579664.29",)),  # one digit changed: missed by it too
        ("100000.00", ("99500.00",)),  # 0.5% off: the old tolerance's edge
        ("0.00", ("0.02",)),
        ("3000.00", ("100.00", "200.00")),  # decimal point shifted
    ],
)
def test_total_off_by_more_than_rounding_fails(total: str, amounts: tuple[str, ...]) -> None:
    checks = check_total(with_amounts(total, *amounts))
    assert rules(checks) == [("total_amount", TOTAL_RULE)]
    assert "rounding allows" in checks[0].detail


@pytest.mark.parametrize(
    ("total", "amounts"),
    [(None, ("1.00",)), ("5.00", ("1.00", None)), ("5.00", ())],
)
def test_total_is_skipped_without_a_complete_sum(
    total: str | None, amounts: tuple[str | None, ...]
) -> None:
    assert check_total(with_amounts(total, *amounts)) == []


# --- weights -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("net", "gross"), [("90.00", "100.00"), ("100.00", "100.00"), (None, "5"), ("5", None)]
)
def test_net_at_or_below_gross_passes(net: str | None, gross: str | None) -> None:
    to_decimal = lambda value: None if value is None else Decimal(value)  # noqa: E731
    assert check_weights(to_decimal(net), to_decimal(gross)) == []


def test_net_above_gross_flags_both_weights() -> None:
    checks = check_weights(Decimal("100.01"), Decimal("100.00"))
    assert rules(checks) == [("net_weight_kg", WEIGHT_RULE), ("gross_weight_kg", WEIGHT_RULE)]


# --- codes ---------------------------------------------------------------------------------


def test_incoterm_outside_the_list_is_flagged() -> None:
    # The schema refuses it; model_copy skips validation, so the check must still hold.
    invoice = make_invoice().model_copy(update={"incoterm": "DDU"})
    assert ("incoterm", INCOTERM_RULE) in rules(validate_invoice(invoice, AS_OF))
    assert check_incoterm("CIF") == []
    assert check_incoterm(None) == []


@pytest.mark.parametrize("currency", ["USD", "PKR", "usd", " EUR ", "XCG", None])
def test_iso_currencies_pass(currency: str | None) -> None:
    assert check_currency(currency) == []


@pytest.mark.parametrize("currency", ["EURO", "Rs", "USD$", "HRK", "XAU", "XXX", ""])
def test_non_iso_withdrawn_and_non_trade_currencies_fail(currency: str) -> None:
    assert rules(check_currency(currency)) == [("currency", CURRENCY_RULE)]


@pytest.mark.parametrize(
    ("party_name", "country"),
    [("shipper", "UK"), ("buyer", "XX"), ("consignee", "PAK"), ("notify_party", "")],
)
def test_country_outside_iso_is_flagged_on_its_party(party_name: str, country: str) -> None:
    invoice = make_invoice(notify_party=party("Clearing Agent"))
    changed = getattr(invoice, party_name).model_copy(update={"country": country})
    invoice = invoice.model_copy(update={party_name: changed})
    assert rules(validate_invoice(invoice, AS_OF)) == [(f"{party_name}.country", COUNTRY_RULE)]


def test_lower_case_country_and_missing_country_pass() -> None:
    invoice = make_invoice(
        shipper=party("Lucas Delmas S.A.R.L.", "fr"),
        buyer=party("Habib Traders").model_copy(update={"country": None}),
    )
    assert validate_invoice(invoice, AS_OF) == []


# --- dates ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "invoice_date", [AS_OF, AS_OF - timedelta(days=1), date(2024, 10, 1), None]
)
def test_dates_from_two_years_back_to_as_of_pass(invoice_date: date | None) -> None:
    assert check_date(invoice_date, AS_OF) == []


@pytest.mark.parametrize(
    ("invoice_date", "reason"),
    [
        (AS_OF + timedelta(days=1), "after"),
        (date(2024, 9, 30), "more than 2 years"),
        (date(1999, 1, 1), "more than 2 years"),
    ],
)
def test_future_and_stale_dates_fail(invoice_date: date, reason: str) -> None:
    checks = check_date(invoice_date, AS_OF)
    assert rules(checks) == [("invoice_date", DATE_RULE)]
    assert reason in checks[0].detail


def test_two_years_before_a_leap_day_is_the_28th() -> None:
    assert years_before(date(2028, 2, 29), 2) == date(2026, 2, 28)
    assert check_date(date(2026, 2, 28), date(2028, 2, 29)) == []
    assert check_date(date(2026, 2, 27), date(2028, 2, 29)) != []


# --- together ------------------------------------------------------------------------------


def test_every_failure_on_one_invoice_is_reported() -> None:
    invoice = make_invoice(
        line_items=[item("2", "100.00", "250.00")],
        total_amount=Decimal("900.00"),
        net_weight_kg=Decimal("200.00"),
        gross_weight_kg=Decimal("100.00"),
        currency="EURO",
        invoice_date=date(2027, 1, 1),
    )
    assert rules(validate_invoice(invoice, AS_OF)) == [
        ("line_items[0].amount", LINE_MATH_RULE),
        ("total_amount", TOTAL_RULE),
        ("net_weight_kg", WEIGHT_RULE),
        ("gross_weight_kg", WEIGHT_RULE),
        ("currency", CURRENCY_RULE),
        ("invoice_date", DATE_RULE),
    ]


def test_validation_never_changes_a_value() -> None:
    invoice = make_invoice(currency="EURO")
    before = invoice.model_dump()
    validate_invoice(invoice, AS_OF)
    assert invoice.model_dump() == before


def test_verify_runs_the_rules_first_then_the_checks() -> None:
    """The unit rule fixes "PIECES" before line math runs, and its own failures are kept."""
    lines = [
        item("2", "100.00", "200.00").model_copy(update={"unit": "PIECES"}),
        item("1", "50.50", "50.50").model_copy(update={"unit": "MET"}),
    ]
    outcome = verify_invoice(make_invoice(line_items=lines), AS_OF)
    assert outcome.invoice.line_items[0].unit == "PCS"
    assert rules(outcome.checks) == [("line_items[1].unit", UNIT_RULE)]


# --- the ISO lists -------------------------------------------------------------------------


def test_country_list_is_every_iso_alpha_2_code() -> None:
    assert len(COUNTRIES) == 249
    assert all(len(code) == 2 and code.isascii() and code.isupper() for code in COUNTRIES)
    assert {"PK", "GB", "US", "CN", "AE", "TR"} <= COUNTRIES
    assert not {"UK", "EU", "XK"} & COUNTRIES  # common non-ISO spellings and user codes


def test_currency_list_is_iso_trade_currencies() -> None:
    assert all(len(code) == 3 and code.isascii() and code.isupper() for code in CURRENCIES)
    assert {"USD", "EUR", "PKR", "CNY", "AED", "SAR", "GBP"} <= CURRENCIES
    assert not {"RMB", "HRK", "VEF", "XAU", "XDR", "XTS", "XXX"} & CURRENCIES


def test_everything_the_generator_uses_is_accepted() -> None:
    assert set(GENERATED_CURRENCIES) <= CURRENCIES
    assert {country.code for country in ALL_COUNTRIES} <= COUNTRIES


# --- party boundary (Salik, 2026-11-17) ------------------------------------------------------

GRAF_ADDRESS = "Nina-Albers-Straße 345 14153 Kehl"
GRAF_PAGE = f"Customer\nGraf, {GRAF_ADDRESS}, Germany\nNotify\nRogner, Mansstr. 71 43477 Oschatz"
NAZARETH_PAGE = (
    "Shipped By\nNazareth, Mistry and Parsa, H.No. 77, Vohra Ganj, Bhubaneswar-770913, India"
)


def with_buyer(name: str | None, address: str | None) -> RawInvoice:
    return make_invoice(buyer=party("x").model_copy(update={"name": name, "address": address}))


def boundary_checks(invoice: RawInvoice, text: str) -> dict[str, str]:
    return {c.path: c.detail for c in check_party_boundaries(invoice, text)}


def test_a_name_ending_inside_a_hyphenated_address_word_is_flagged() -> None:
    """inv_00014: the model read "Graf, Nina-Albers-Straße" as name "Graf, Nina"."""
    checks = check_party_boundaries(
        with_buyer("Graf, Nina", "Albers-Straße 345 14153 Kehl"), GRAF_PAGE
    )
    assert [(c.path, c.rule, c.status) for c in checks] == [
        ("buyer.name", PARTY_BOUNDARY_RULE, "failed_check"),
        ("buyer.address", PARTY_BOUNDARY_RULE, "failed_check"),
    ]
    assert "splits a word" in checks[0].detail


def test_a_word_in_both_the_name_and_the_address_is_flagged() -> None:
    """inv_00014's notify party: name "Rogner, Mans", address "Mansstr. 71 …"."""
    invoice = make_invoice(
        notify_party=party("x").model_copy(
            update={"name": "Rogner, Mans", "address": "Mansstr. 71 43477 Oschatz"}
        )
    )
    detail = boundary_checks(invoice, GRAF_PAGE)["notify_party.name"]
    assert "in both" in detail


def test_words_between_name_and_address_on_one_line_are_flagged() -> None:
    """A name cut at a comma leaves its last words to neither field."""
    invoice = make_invoice(
        shipper=party("x").model_copy(
            update={"name": "Nazareth", "address": "H.No. 77, Vohra Ganj, Bhubaneswar-770913"}
        )
    )
    detail = boundary_checks(invoice, NAZARETH_PAGE)["shipper.name"]
    assert "'Mistry and Parsa' is printed between them" in detail


@pytest.mark.parametrize(
    ("name", "address", "page"),
    [
        ("Graf", GRAF_ADDRESS, GRAF_PAGE),
        ("Nazareth, Mistry and Parsa", "H.No. 77, Vohra Ganj, Bhubaneswar-770913", NAZARETH_PAGE),
        # an answer that adds a comma the page doesn't print (diag_00001) still matches
        (
            "Vohra, Mallick and Tank",
            "H.No. 39, Kapadia Road, Jalandhar-305441",
            "Vohra, Mallick and Tank, H.No. 39, Kapadia Road Jalandhar-305441, India",
        ),
        # the manifest layout's separator
        ("Graf", GRAF_ADDRESS, f"Customer Graf | {GRAF_ADDRESS} | Germany"),
        # a short name that also starts the address: one right printing is enough
        ("Graf", "Grafenweg 5 12345 Kehl", "Graf, Grafenweg 5 12345 Kehl, Germany"),
        # name and address on their own lines, another column's text beside the name
        ("Graf", GRAF_ADDRESS, f"Graf Invoice No 757-39435\n{GRAF_ADDRESS}\nGermany"),
    ],
)
def test_a_right_boundary_is_not_flagged(name: str, address: str, page: str) -> None:
    assert check_party_boundaries(with_buyer(name, address), page) == []


@pytest.mark.parametrize(
    ("name", "address"),
    [
        ("Graf", None),  # nothing to compare
        ("Somebody Else", GRAF_ADDRESS),  # the page never prints this name
        ("Graf", "Elsewhere 1 99999 Nowhere"),  # nor this address after it
    ],
)
def test_a_party_the_page_doesnt_print_as_extracted_is_not_checked(
    name: str, address: str | None
) -> None:
    assert check_party_boundaries(with_buyer(name, address), GRAF_PAGE) == []


def test_boundary_check_runs_only_with_a_text_layer_and_never_changes_values() -> None:
    wrong = with_buyer("Graf, Nina", "Albers-Straße 345 14153 Kehl")
    assert [c.rule for c in validate_invoice(wrong, AS_OF)] == []
    outcome = verify_invoice(wrong, AS_OF, text_layer=GRAF_PAGE)
    assert {c.path for c in outcome.checks} == {"buyer.name", "buyer.address"}
    assert outcome.invoice.buyer == wrong.buyer


ONE_LINE_PARTY_LAYOUTS = ("ledger", "manifest")


@pytest.mark.parametrize("template", ONE_LINE_PARTY_LAYOUTS)
def test_inv_00014s_answer_is_flagged_on_a_rendered_page(template: str) -> None:
    truth = with_buyer("Graf", GRAF_ADDRESS)
    with open_browser_page() as page:
        html = render_html(truth, template, pick_labels(random.Random(1)))
        text = prepare_document(render_pdf(page, html)).text_layer
    assert text is not None
    assert check_party_boundaries(truth, text) == []
    answer = with_buyer("Graf, Nina", "Albers-Straße 345 14153 Kehl")
    assert {c.path for c in check_party_boundaries(answer, text)} == {"buyer.name", "buyer.address"}


@pytest.mark.parametrize("template", TEMPLATES)
def test_ground_truth_never_raises_a_boundary_alarm_on_a_rendered_page(template: str) -> None:
    catalogue = load_catalogue()
    with open_browser_page() as page:
        for index in range(4):
            truth = generate_ground_truth(42, index, catalogue)
            html = render_html(
                truth.invoice, template, pick_labels(random.Random(index)), truth.buyer_reference
            )
            text = prepare_document(render_pdf(page, html)).text_layer
            assert text is not None
            assert check_party_boundaries(truth.invoice, text) == [], (template, index)
