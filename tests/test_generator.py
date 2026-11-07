from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.constants import INCOTERMS
from synth.generator import (
    ANCHOR_DATE,
    CENT,
    COUNTRY_NAMES,
    CURRENCIES,
    MAX_INVOICE_AGE_DAYS,
    MAX_LINE_ITEMS,
    MIN_LINE_ITEMS,
    PAKISTAN,
    CatalogueItem,
    GroundTruth,
    generate_ground_truth,
    load_catalogue,
)

SEED = 42
SAMPLE_SIZE = 300


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
    path.write_text("description,hs_code\nWidget,not-a-code\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_catalogue(path)


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
        assert invoice.shipper.country in COUNTRY_NAMES
        assert invoice.consignee.country in COUNTRY_NAMES


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
        for party in (document.invoice.shipper, document.invoice.consignee):
            assert party.name
            assert party.address
            assert "\n" not in party.address


def test_pakistan_addresses_name_a_pakistani_city(documents: list[GroundTruth]) -> None:
    parties = [
        party
        for document in documents
        for party in (document.invoice.shipper, document.invoice.consignee)
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
