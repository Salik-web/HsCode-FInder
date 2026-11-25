"""Deterministic rules after validation: reference buyers and units."""

from datetime import date
from decimal import Decimal

import pytest

from app.pipeline.rules import (
    HS_CODE_CONFLICT_RULE,
    REFERENCE_RULE,
    UNIT_RULE,
    UNIT_SYNONYMS,
    apply_rules,
    check_unit,
    is_reference_phrase,
    map_code_synonyms,
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
    [
        ("PIECES", "PCS"),
        ("pcs", "PCS"),
        ("Pcs.", "PCS"),
        ("KGS", "KG"),
        ("Metric Tons", "MT"),
    ],
)
def test_exact_synonyms_and_case_map_to_the_list(printed: str, canonical: str) -> None:
    unit, change, check = check_unit(printed, "line_items[0].unit")
    assert unit == canonical
    assert change == f"line_items[0].unit: {printed!r} -> {canonical!r}"
    assert check is None


@pytest.mark.parametrize("printed", ["MET", "SE", "M", "TON", "PCSS", "BOX", "LTR"])
def test_unknown_and_near_miss_units_fail_the_check_and_keep_their_value(printed: str) -> None:
    unit, change, check = check_unit(printed, "line_items[2].unit")
    assert unit == printed  # never guessed
    assert change is None
    assert check is not None
    assert check.status == "failed_check"
    assert check.rule == UNIT_RULE
    assert check.path == "line_items[2].unit"


@pytest.mark.parametrize("printed", ["Ea.", "EA.", "ea", "EACH", "Each"])
def test_each_is_its_own_unit(printed: str) -> None:
    """Not PCS or NOS until Salik's operations contacts say so (2026-11-25)."""
    unit, change, check = check_unit(printed, "u")
    assert (unit, check) == ("EA", None)
    assert change == f"u: {printed!r} -> 'EA'"


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


@pytest.mark.parametrize("printed", ["NOS", "nos", "Nos."])
def test_nos_is_its_own_unit_not_a_synonym_of_pcs(printed: str) -> None:
    assert check_unit(printed, "u")[0] == "NOS"
    assert check_unit(printed, "u")[2] is None
    assert "NOS" not in UNIT_SYNONYMS


@pytest.mark.parametrize("printed", ["TON", "TONS", "M"])
def test_ambiguous_units_stay_failing(printed: str) -> None:
    """TON (short, long or metric) and M (metre or thousand) have no single meaning."""
    assert check_unit(printed, "u")[2] is not None


# --- code synonyms -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "printed", "code"),
    [
        ("incoterm", "C&F", "CFR"),
        ("incoterm", "CNF", "CFR"),
        ("incoterm", " c&f ", "CFR"),
        ("currency", "RMB", "CNY"),
        ("currency", "US$", "USD"),
        ("currency", "rmb", "CNY"),
    ],
)
def test_exact_code_synonyms_map_and_record_the_printed_value(
    field: str, printed: str, code: str
) -> None:
    mapped, changes = map_code_synonyms({field: printed, "other": "kept"})
    assert mapped == {field: code, "other": "kept"}
    assert changes == [f"{field}: {printed!r} -> {code!r}"]


@pytest.mark.parametrize(
    ("field", "printed"),
    [
        ("incoterm", "DDU"),  # not the same rule as DAP
        ("incoterm", "C & F"),  # not an exact synonym
        ("incoterm", "CFR"),  # already the code
        ("currency", "Rs"),  # Pakistani, Indian, Sri Lankan ... rupee: ambiguous
        ("currency", "EURO"),
        ("currency", "CNY"),
    ],
)
def test_everything_else_is_left_as_printed(field: str, printed: str) -> None:
    assert map_code_synonyms({field: printed}) == ({field: printed}, [])


def test_synonym_mapping_never_touches_missing_or_non_text_values() -> None:
    data = {"incoterm": None, "currency": 5}
    assert map_code_synonyms(data) == (data, [])
    assert map_code_synonyms(["not", "a", "dict"]) == (["not", "a", "dict"], [])


def test_every_synonym_maps_to_an_accepted_code() -> None:
    from app.pipeline.rules import CURRENCY_SYNONYMS, INCOTERM_SYNONYMS
    from app.schemas.constants import INCOTERMS
    from app.schemas.iso_codes import CURRENCIES

    assert set(INCOTERM_SYNONYMS.values()) <= set(INCOTERMS)
    assert set(CURRENCY_SYNONYMS.values()) <= CURRENCIES


# --- HS codes printed in the description ---------------------------------------------------


def with_goods(description: str | None, code: str | None) -> RawInvoice:
    base = invoice("Lucas Delmas")
    line = base.line_items[0].model_copy(
        update={"description": description, "hs_code_on_doc": code}
    )
    return base.model_copy(update={"line_items": [line]})


def test_a_code_in_the_description_moves_to_the_empty_code_field() -> None:
    outcome = apply_rules(with_goods("Cotton towels - 6302.60.0010.3", None))
    [line] = outcome.invoice.line_items
    assert (line.description, line.hs_code_on_doc) == ("Cotton towels", "6302.60.0010.3")
    assert outcome.applied == [
        "line_items[0].description: 'Cotton towels - 6302.60.0010.3' -> 'Cotton towels'",
        "line_items[0].hs_code_on_doc: 6302.60.0010.3 taken from the end of the description",
    ]
    assert outcome.checks == []


def test_a_code_printed_in_both_places_leaves_the_description_only() -> None:
    outcome = apply_rules(with_goods("Shirts - 6105.10.4000.8", "6105.10.4000.8"))
    [line] = outcome.invoice.line_items
    assert (line.description, line.hs_code_on_doc) == ("Shirts", "6105.10.4000.8")
    assert len(outcome.applied) == 1


def test_two_different_codes_move_nothing_and_fail_the_check() -> None:
    outcome = apply_rules(with_goods("Shirts - 6105.10.4000", "8708.29.0000"))
    [line] = outcome.invoice.line_items
    assert (line.description, line.hs_code_on_doc) == ("Shirts - 6105.10.4000", "8708.29.0000")
    [check] = outcome.checks
    assert (check.path, check.rule) == ("line_items[0].hs_code_on_doc", HS_CODE_CONFLICT_RULE)
    assert "6105.10.4000" in check.detail
    assert "8708.29.0000" in check.detail


def test_a_heading_outside_the_tariff_is_not_a_code() -> None:
    goods = with_goods("Cable drum - 1250.50", None)
    assert apply_rules(goods, frozenset({"6105"})).invoice == goods


def test_a_missing_description_is_left_alone() -> None:
    goods = with_goods(None, "6105.10")
    assert apply_rules(goods).invoice == goods
