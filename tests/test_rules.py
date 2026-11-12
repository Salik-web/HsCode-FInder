"""Deterministic rules after validation: reference buyers and units."""

from datetime import date
from decimal import Decimal

import pytest

from app.pipeline.rules import (
    REFERENCE_RULE,
    UNIT_RULE,
    UNIT_SYNONYMS,
    apply_rules,
    check_unit,
    is_reference_phrase,
)
from app.schemas.constants import UNITS
from app.schemas.raw import RawInvoice, RawLineItem, RawParty

CONSIGNEE = RawParty(name="Omeir-Luqmaan", address="Plot 93, Block C, Multan 81714", country="PK")


def invoice(buyer_name: str | None = "As consignee", units: tuple[str | None, ...] = ("PCS",)):
    lines = [
        RawLineItem(
            description="Tyres",
            quantity=Decimal(1),
            unit=unit,
            unit_price=Decimal(1),
            amount=Decimal(1),
            hs_code_on_doc=None,
        )
        for unit in units
    ]
    return RawInvoice(
        invoice_number="INV-1",
        invoice_date=date(2026, 1, 2),
        shipper=RawParty(name="Lucas Delmas", address="Paris", country="FR"),
        buyer=RawParty(name=buyer_name, address=None, country=None),
        consignee=CONSIGNEE,
        notify_party=None,
        incoterm="CIF",
        currency="EUR",
        line_items=lines,
        total_amount=Decimal(1),
        gross_weight_kg=None,
        net_weight_kg=None,
    )


# --- reference buyers ------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "Same as consignee",
        "As consignee",
        "As per consignee",
        "Same as consignee details",
        "As per consignee details",
        "SAME AS CONSIGNEE",
        "Same as Consignee.",
        "same as the consignee",
        "Same as ship to",
        "As per Deliver To",
        "Same as Delivery Address",
        "  as   consignee ",
    ],
)
def test_reference_phrases_match(phrase: str) -> None:
    assert is_reference_phrase(phrase)


@pytest.mark.parametrize(
    "name",
    [
        "Consignee Logistics Ltd",  # a real company with the word in its name
        "As Per Trading Co",
        "Same Day Couriers",
        "Asconsignee Holdings",
        "Same as above",  # "above" could be the shipper: not a safe reference
        "Same as shipper",
        "Omeir-Luqmaan",
        "",
        None,
    ],
)
def test_company_names_and_unsafe_references_do_not_match(name: str | None) -> None:
    assert not is_reference_phrase(name)


def test_reference_buyer_becomes_the_consignee_and_is_recorded() -> None:
    outcome = apply_rules(invoice("As consignee"))
    assert outcome.invoice.buyer == CONSIGNEE
    assert outcome.applied == [REFERENCE_RULE]


def test_real_buyer_is_left_alone() -> None:
    outcome = apply_rules(invoice("Consignee Logistics Ltd"))
    assert outcome.invoice.buyer.name == "Consignee Logistics Ltd"
    assert outcome.applied == []


def test_reference_with_no_consignee_name_is_left_as_extracted() -> None:
    no_consignee = invoice("As consignee").model_copy(
        update={"consignee": RawParty(name=None, address=None, country=None)}
    )
    outcome = apply_rules(no_consignee)
    assert outcome.invoice.buyer.name == "As consignee"
    assert outcome.applied == []


# --- units -----------------------------------------------------------------------------


@pytest.mark.parametrize("unit", UNITS)
def test_every_known_unit_passes_unchanged(unit: str) -> None:
    assert check_unit(unit, "u") == (unit, None, None)


@pytest.mark.parametrize(
    ("printed", "canonical"),
    [("PIECES", "PCS"), ("pcs", "PCS"), ("Pcs.", "PCS"), ("KGS", "KG"), ("Metric Tons", "MT")],
)
def test_exact_synonyms_and_case_map_to_the_list(printed: str, canonical: str) -> None:
    unit, change, check = check_unit(printed, "line_items[0].unit")
    assert unit == canonical
    assert change == f"line_items[0].unit: {printed!r} -> {canonical!r}"
    assert check is None


@pytest.mark.parametrize("printed", ["MET", "SE", "M", "TON", "NOS", "PCSS", "BOX", "LTR"])
def test_unknown_and_near_miss_units_fail_the_check_and_keep_their_value(printed: str) -> None:
    unit, change, check = check_unit(printed, "line_items[2].unit")
    assert unit == printed  # never guessed
    assert change is None
    assert check is not None
    assert check.status == "failed_check"
    assert check.rule == UNIT_RULE
    assert check.path == "line_items[2].unit"


def test_every_synonym_maps_into_the_unit_list() -> None:
    assert set(UNIT_SYNONYMS.values()) <= set(UNITS)
    assert not set(UNIT_SYNONYMS) & set(UNITS)


def test_missing_unit_is_neither_changed_nor_failed() -> None:
    assert check_unit(None, "u") == (None, None, None)


def test_rules_report_every_unit_line_by_line() -> None:
    outcome = apply_rules(invoice("Omeir-Luqmaan", units=("PIECES", "MET", "KG", "SE")))
    assert [line.unit for line in outcome.invoice.line_items] == ["PCS", "MET", "KG", "SE"]
    assert [check.path for check in outcome.checks] == [
        "line_items[1].unit",
        "line_items[3].unit",
    ]
    assert outcome.applied == ["line_items[0].unit: 'PIECES' -> 'PCS'"]
