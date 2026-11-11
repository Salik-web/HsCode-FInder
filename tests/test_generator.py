import re
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.constants import INCOTERMS
from app.schemas.raw import RawInvoice, RawParty
from synth import generator
from synth.generator import (
    ANCHOR_DATE,
    BUYER_REFERENCE_PHRASES,
    CENT,
    COUNTRY_NAMES,
    CURRENCIES,
    MAX_INVOICE_AGE_DAYS,
    MAX_LINE_ITEMS,
    MAX_LINE_WEIGHT_KG,
    MAX_QUANTITY,
    MEASURED_UNITS,
    MIN_LINE_ITEMS,
    MIN_QUANTITY,
    PAKISTAN,
    CatalogueItem,
    GroundTruth,
    generate_ground_truth,
    load_catalogue,
)

SEED = 42
SAMPLE_SIZE = 300
YEAR_IN_INVOICE_NUMBER = re.compile(r"^(?:INV-(?P<long>\d{4})-|(?:CI/|EXP)(?P<short>\d{2}))")


def parties_of(invoice: RawInvoice) -> list[RawParty]:
    parties = [invoice.shipper, invoice.buyer, invoice.consignee, invoice.notify_party]
    return [party for party in parties if party is not None]


@pytest.fixture(scope="module")
def catalogue() -> list[CatalogueItem]:
    return load_catalogue()


@pytest.fixture(scope="module")
def documents(catalogue: list[CatalogueItem]) -> list[GroundTruth]:
    return [generate_ground_truth(SEED, index, catalogue) for index in range(SAMPLE_SIZE)]


def test_catalogue_has_placeholder_rows(catalogue: list[CatalogueItem]) -> None:
    assert len(catalogue) >= MAX_LINE_ITEMS
    assert len({item.description for item in catalogue}) == len(catalogue)


def test_catalogue_keeps_commas_inside_quoted_descriptions(
    catalogue: list[CatalogueItem],
) -> None:
    assert any("," in item.description for item in catalogue)


def test_catalogue_rejects_malformed_hs_code(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(
        "description,hs_code,unit,kg_per_unit\nWidget,not-a-code,PCS,1\n", encoding="utf-8"
    )
    with pytest.raises(ValidationError):
        load_catalogue(path)


@pytest.mark.parametrize(
    "row",
    ["Widget,8471.3010,BUCKET,1", "Widget,8471.3010,PCS,0", "Widget,8471.3010,PCS,-1"],
)
def test_catalogue_rejects_bad_unit_or_weight(tmp_path: Path, row: str) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(f"description,hs_code,unit,kg_per_unit\n{row}\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_catalogue(path)


def test_lines_use_the_catalogue_unit(
    documents: list[GroundTruth], catalogue: list[CatalogueItem]
) -> None:
    unit_for = {item.description: item.unit for item in catalogue}
    for document in documents:
        for line in document.invoice.line_items:
            assert line.unit == unit_for[line.description]


def test_counted_units_have_whole_quantities(documents: list[GroundTruth]) -> None:
    for document in documents:
        for line in document.invoice.line_items:
            if line.unit not in MEASURED_UNITS:
                assert line.quantity == line.quantity.to_integral_value()


def test_quantities_stay_positive_and_under_the_line_weight_limit(
    documents: list[GroundTruth], catalogue: list[CatalogueItem]
) -> None:
    kg_for = {item.description: item.kg_per_unit for item in catalogue}
    for document in documents:
        for line in document.invoice.line_items:
            assert line.quantity >= MIN_QUANTITY
            assert line.quantity <= MAX_QUANTITY
            assert line.quantity * kg_for[line.description] <= MAX_LINE_WEIGHT_KG


def test_item_heavier_than_the_line_limit_still_gets_quantity_one() -> None:
    too_heavy = CatalogueItem(
        description="Transformer", hs_code="8504.2300", unit="PCS", kg_per_unit=Decimal(5000)
    )
    line = generate_ground_truth(SEED, 0, [too_heavy]).invoice.line_items[0]
    assert line.quantity == MIN_QUANTITY


def test_net_weight_is_the_sum_of_line_weights(
    documents: list[GroundTruth], catalogue: list[CatalogueItem]
) -> None:
    kg_for = {item.description: item.kg_per_unit for item in catalogue}
    for document in documents:
        invoice = document.invoice
        exact = sum(line.quantity * kg_for[line.description] for line in invoice.line_items)
        assert invoice.net_weight_kg == exact.quantize(CENT, rounding=ROUND_HALF_UP)


def test_gross_weight_adds_three_to_ten_percent_packaging(documents: list[GroundTruth]) -> None:
    half_cent = CENT / 2
    for document in documents:
        net = document.invoice.net_weight_kg
        gross = document.invoice.gross_weight_kg
        assert net * Decimal("1.03") - half_cent <= gross <= net * Decimal("1.10") + half_cent


def test_year_in_invoice_number_matches_invoice_date(documents: list[GroundTruth]) -> None:
    checked_years = set()
    for document in documents:
        invoice = document.invoice
        match = YEAR_IN_INVOICE_NUMBER.match(invoice.invoice_number)
        if match:
            year = match.group("long") or f"20{match.group('short')}"
            assert int(year) == invoice.invoice_date.year
            checked_years.add(int(year))
    assert checked_years == {2025, 2026}


def test_line_counts_stay_in_bounds_and_reach_both_ends(documents: list[GroundTruth]) -> None:
    counts = {len(document.invoice.line_items) for document in documents}
    assert min(counts) == MIN_LINE_ITEMS
    assert max(counts) == MAX_LINE_ITEMS


def test_line_count_is_capped_by_a_small_catalogue(catalogue: list[CatalogueItem]) -> None:
    small = catalogue[:2]
    for index in range(50):
        assert len(generate_ground_truth(SEED, index, small).invoice.line_items) <= 2


def test_line_amounts_equal_quantity_times_unit_price(documents: list[GroundTruth]) -> None:
    for document in documents:
        for line in document.invoice.line_items:
            expected = (line.quantity * line.unit_price).quantize(CENT, rounding=ROUND_HALF_UP)
            assert line.amount == expected


def test_total_equals_sum_of_line_amounts(documents: list[GroundTruth]) -> None:
    for document in documents:
        invoice = document.invoice
        assert invoice.total_amount == sum(line.amount for line in invoice.line_items)


def test_money_is_decimal_with_two_places(documents: list[GroundTruth]) -> None:
    for document in documents:
        invoice = document.invoice
        money = [invoice.total_amount, *(line.amount for line in invoice.line_items)]
        for value in money:
            assert isinstance(value, Decimal)
            assert value.as_tuple().exponent == -2


def test_net_weight_never_exceeds_gross_weight(documents: list[GroundTruth]) -> None:
    for document in documents:
        assert document.invoice.net_weight_kg > 0
        assert document.invoice.net_weight_kg <= document.invoice.gross_weight_kg


def test_categorical_fields_come_from_allowed_lists(documents: list[GroundTruth]) -> None:
    for document in documents:
        invoice = document.invoice
        assert invoice.incoterm in INCOTERMS
        assert invoice.currency in CURRENCIES
        for party in parties_of(invoice):
            assert party.country in COUNTRY_NAMES


def test_every_incoterm_and_currency_is_used(documents: list[GroundTruth]) -> None:
    assert {document.invoice.incoterm for document in documents} == set(INCOTERMS)
    assert {document.invoice.currency for document in documents} == set(CURRENCIES)


def test_shipper_and_consignee_are_in_different_countries(documents: list[GroundTruth]) -> None:
    for document in documents:
        assert document.invoice.shipper.country != document.invoice.consignee.country


def test_most_invoices_involve_pakistan(documents: list[GroundTruth]) -> None:
    involved = [
        document
        for document in documents
        if "PK" in (document.invoice.shipper.country, document.invoice.consignee.country)
    ]
    assert len(involved) > SAMPLE_SIZE * 0.7


def test_dates_stay_inside_the_fixed_window(documents: list[GroundTruth]) -> None:
    earliest = ANCHOR_DATE - timedelta(days=MAX_INVOICE_AGE_DAYS)
    for document in documents:
        assert earliest <= document.invoice.invoice_date <= ANCHOR_DATE


def test_party_text_is_single_line_and_not_empty(documents: list[GroundTruth]) -> None:
    for document in documents:
        for party in parties_of(document.invoice):
            assert party.name
            assert party.address
            assert "\n" not in party.address


def test_buyer_is_the_consignee_about_sixty_percent_of_the_time(
    documents: list[GroundTruth],
) -> None:
    same = sum(document.invoice.buyer == document.invoice.consignee for document in documents)
    assert 0.52 <= same / SAMPLE_SIZE <= 0.68


def test_a_different_buyer_is_never_in_the_shippers_country(documents: list[GroundTruth]) -> None:
    for document in documents:
        invoice = document.invoice
        if invoice.buyer != invoice.consignee:
            assert invoice.buyer.name != invoice.consignee.name
            assert invoice.buyer.country != invoice.shipper.country


def test_different_buyers_are_local_or_in_a_third_country(documents: list[GroundTruth]) -> None:
    placements = {
        document.invoice.buyer.country == document.invoice.consignee.country
        for document in documents
        if document.invoice.buyer != document.invoice.consignee
    }
    assert placements == {True, False}


def test_notify_party_is_optional_and_in_the_consignees_country(
    documents: list[GroundTruth],
) -> None:
    notify_parties = [
        document.invoice for document in documents if document.invoice.notify_party is not None
    ]
    assert 0.3 <= len(notify_parties) / SAMPLE_SIZE <= 0.5
    for invoice in notify_parties:
        assert invoice.notify_party.country == invoice.consignee.country
        assert invoice.notify_party != invoice.consignee


@pytest.mark.parametrize("probability", [0.0, 1.0])
def test_party_choices_never_change_other_fields(
    catalogue: list[CatalogueItem], monkeypatch: pytest.MonkeyPatch, probability: float
) -> None:
    """Buyer and notify party draw from their own stream, so forcing them changes nothing else."""
    party_fields = {"buyer", "notify_party"}
    normal = [generate_ground_truth(SEED, i, catalogue).invoice for i in range(30)]
    monkeypatch.setattr(generator, "BUYER_IS_CONSIGNEE_PROBABILITY", probability)
    monkeypatch.setattr(generator, "NOTIFY_PARTY_PROBABILITY", probability)
    forced = [generate_ground_truth(SEED, i, catalogue).invoice for i in range(30)]
    assert [i.model_dump(exclude=party_fields) for i in forced] == [
        i.model_dump(exclude=party_fields) for i in normal
    ]
    assert [i.model_dump(include=party_fields) for i in forced] != [
        i.model_dump(include=party_fields) for i in normal
    ]


def test_about_thirty_percent_of_shared_buyers_are_printed_as_a_reference(
    documents: list[GroundTruth],
) -> None:
    shared = [d for d in documents if d.invoice.buyer == d.invoice.consignee]
    referenced = [d for d in shared if d.buyer_reference is not None]
    assert 0.2 <= len(referenced) / len(shared) <= 0.4
    assert {d.buyer_reference for d in referenced} == set(BUYER_REFERENCE_PHRASES)


def test_only_a_buyer_identical_to_the_consignee_is_printed_as_a_reference(
    documents: list[GroundTruth],
) -> None:
    for document in documents:
        if document.buyer_reference is not None:
            assert document.invoice.buyer == document.invoice.consignee


def test_ground_truth_rejects_a_reference_to_a_different_company(
    documents: list[GroundTruth],
) -> None:
    distinct = next(d for d in documents if d.invoice.buyer != d.invoice.consignee)
    with pytest.raises(ValidationError, match="buyer_reference"):
        GroundTruth(
            invoice=distinct.invoice,
            true_hs_codes=distinct.true_hs_codes,
            buyer_reference=BUYER_REFERENCE_PHRASES[0],
        )


@pytest.mark.parametrize("probability", [0.0, 1.0])
def test_buyer_reference_never_changes_the_invoice(
    catalogue: list[CatalogueItem], monkeypatch: pytest.MonkeyPatch, probability: float
) -> None:
    normal = [generate_ground_truth(SEED, i, catalogue) for i in range(30)]
    monkeypatch.setattr(generator, "BUYER_REFERENCE_PROBABILITY", probability)
    forced = [generate_ground_truth(SEED, i, catalogue) for i in range(30)]
    assert [d.invoice for d in forced] == [d.invoice for d in normal]
    assert [d.buyer_reference for d in forced] != [d.buyer_reference for d in normal]


def test_pakistan_addresses_name_a_pakistani_city(documents: list[GroundTruth]) -> None:
    parties = [
        party
        for document in documents
        for party in parties_of(document.invoice)
        if party.country == "PK"
    ]
    assert parties
    for party in parties:
        assert any(city in party.address for city in PAKISTAN.cities)


def test_hs_codes_align_with_catalogue_descriptions(
    documents: list[GroundTruth], catalogue: list[CatalogueItem]
) -> None:
    code_for = {item.description: item.hs_code for item in catalogue}
    for document in documents:
        for line, true_code in zip(
            document.invoice.line_items, document.true_hs_codes, strict=True
        ):
            assert code_for[line.description] == true_code


def test_hs_codes_are_printed_on_some_documents_only(documents: list[GroundTruth]) -> None:
    printed = [
        all(line.hs_code_on_doc for line in document.invoice.line_items) for document in documents
    ]
    assert any(printed)
    assert not all(printed)


def test_printed_hs_code_is_the_true_code(documents: list[GroundTruth]) -> None:
    for document in documents:
        for line, true_code in zip(
            document.invoice.line_items, document.true_hs_codes, strict=True
        ):
            assert line.hs_code_on_doc in (None, true_code)


def test_lines_on_a_document_are_unique(documents: list[GroundTruth]) -> None:
    for document in documents:
        descriptions = [line.description for line in document.invoice.line_items]
        assert len(descriptions) == len(set(descriptions))


def test_output_is_schema_valid_after_json_round_trip(documents: list[GroundTruth]) -> None:
    for document in documents:
        assert GroundTruth.model_validate_json(document.model_dump_json()) == document


def test_ground_truth_rejects_misaligned_hs_codes(documents: list[GroundTruth]) -> None:
    payload = documents[0].model_dump()
    payload["true_hs_codes"].append("0000.0000")
    with pytest.raises(ValidationError):
        GroundTruth.model_validate(payload)


def test_same_seed_gives_same_output(catalogue: list[CatalogueItem]) -> None:
    first = [generate_ground_truth(SEED, index, catalogue) for index in range(20)]
    second = [generate_ground_truth(SEED, index, catalogue) for index in range(20)]
    assert first == second


def test_different_seed_gives_different_output(catalogue: list[CatalogueItem]) -> None:
    first = [generate_ground_truth(1, index, catalogue) for index in range(20)]
    second = [generate_ground_truth(2, index, catalogue) for index in range(20)]
    assert first != second


def test_document_does_not_depend_on_generation_order(catalogue: list[CatalogueItem]) -> None:
    in_order = [generate_ground_truth(SEED, index, catalogue) for index in range(10)]
    alone = generate_ground_truth(SEED, 7, catalogue)
    assert alone == in_order[7]
