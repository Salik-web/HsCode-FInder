"""Key repair: strict, unambiguous renames only; everything else unknown is dropped."""

import pytest
from pydantic import BaseModel

from app.pipeline.repair import (
    AMBIGUITY_MARGIN,
    KEY_MATCH_THRESHOLD,
    NO_REPAIRS,
    describe_allowed_keys,
    key_similarity,
    repair_keys,
)
from app.schemas.compact import LINE_COLUMNS, CompactInvoice
from app.schemas.raw import RawInvoice, RawParty

PARTY = {"name": "Habib Traders", "address": "Plot 1, Karachi", "country": "PK"}


def answer(**changes: object) -> dict:
    """A well-formed compact answer, as the model would send it."""
    data: dict = {
        "invoice_number": "INV-1",
        "invoice_date": "2026-01-02",
        "shipper": dict(PARTY),
        "buyer": dict(PARTY),
        "consignee": dict(PARTY),
        "notify_party": None,
        "incoterm": "CIF",
        "currency": "USD",
        "line_items": [["Tyres", "2", "PCS", "10.50", "21.00", None]],
        "total_amount": "21.00",
        "gross_weight_kg": None,
        "net_weight_kg": None,
    }
    return data | changes


def rename(data: dict, old: str, new: str) -> dict:
    return {new if key == old else key: value for key, value in data.items()}


def test_well_formed_answer_is_untouched() -> None:
    data = answer()
    repaired, repairs = repair_keys(data, CompactInvoice)
    assert repaired == data
    assert repairs.repaired == repairs.ignored == []
    CompactInvoice.model_validate(repaired)


@pytest.mark.parametrize("bad_key", [" shipper", "Shipper", "SHIPPER\t", "shiper", "shippper"])
def test_near_miss_key_is_repaired(bad_key: str) -> None:
    repaired, repairs = repair_keys(rename(answer(), "shipper", bad_key), CompactInvoice)
    assert repaired["shipper"] == PARTY
    assert repairs.repaired == [f"(top level): {bad_key!r} -> 'shipper'"]
    assert repairs.ignored == []
    CompactInvoice.model_validate(repaired)


def test_nested_party_keys_are_repaired_with_their_path() -> None:
    buyer = {"name": "A", "adress": "B", "country": "PK"}
    repaired, repairs = repair_keys(answer(buyer=buyer), CompactInvoice)
    assert repaired["buyer"] == {"name": "A", "address": "B", "country": "PK"}
    assert repairs.repaired == ["buyer: 'adress' -> 'address'"]


def test_unknown_keys_are_dropped_and_counted() -> None:
    data = answer(document_type="Commercial Invoice", notes="x")
    data["buyer"] = dict(PARTY) | {"country_of_origin": "CN"}
    repaired, repairs = repair_keys(data, CompactInvoice)
    assert "document_type" not in repaired
    assert "country_of_origin" not in repaired["buyer"]
    assert sorted(repairs.ignored) == ["buyer.country_of_origin", "document_type", "notes"]
    assert repairs.repaired == []
    CompactInvoice.model_validate(repaired)


def test_key_below_the_threshold_is_dropped_not_guessed() -> None:
    """`shiftper` scores 0.80 against `shipper`: too far to rename safely."""
    assert key_similarity("shiftper", "shipper") < KEY_MATCH_THRESHOLD
    repaired, repairs = repair_keys(rename(answer(), "shipper", "shiftper"), CompactInvoice)
    assert "shipper" not in repaired
    assert repairs.ignored == ["shiftper"]


class Weights(BaseModel):
    """Two keys a single misspelling can be equally close to."""

    weight_net_kg: str | None
    weight_net_lb: str | None


def test_ambiguous_near_miss_between_two_known_keys_is_not_repaired() -> None:
    first = key_similarity("weight_net_", "weight_net_kg")
    second = key_similarity("weight_net_", "weight_net_lb")
    assert min(first, second) >= KEY_MATCH_THRESHOLD  # each alone would be repaired
    assert abs(first - second) < AMBIGUITY_MARGIN
    repaired, repairs = repair_keys({"weight_net_": "12.5"}, Weights)
    assert repaired == {}
    assert repairs.repaired == []
    assert repairs.ignored == ["weight_net_"]


def test_clear_winner_between_two_similar_keys_is_repaired() -> None:
    repaired, repairs = repair_keys({"weight_net_kgs": "12.5"}, Weights)
    assert repaired == {"weight_net_kg": "12.5"}
    assert repairs.repaired == ["(top level): 'weight_net_kgs' -> 'weight_net_kg'"]


def test_vague_prefix_of_two_schema_keys_is_dropped() -> None:
    """`invoice_` is closest to invoice_number (0.80) but below the threshold."""
    data = answer()
    del data["invoice_number"], data["invoice_date"]
    data["invoice_"] = "INV-1"
    repaired, repairs = repair_keys(data, CompactInvoice)
    assert "invoice_number" not in repaired
    assert repairs.repaired == []
    assert repairs.ignored == ["invoice_"]


def test_two_unknown_keys_claiming_one_target_are_both_dropped() -> None:
    data = rename(answer(), "shipper", "shiper")
    data["shippr"] = dict(PARTY)
    repaired, repairs = repair_keys(data, CompactInvoice)
    assert "shipper" not in repaired
    assert sorted(repairs.ignored) == ["shiper", "shippr"]
    assert repairs.repaired == []


def test_a_key_already_present_is_never_overwritten() -> None:
    data = answer()
    data[" shipper"] = {"name": "Someone else", "address": None, "country": None}
    repaired, repairs = repair_keys(data, CompactInvoice)
    assert repaired["shipper"] == PARTY
    assert repairs.ignored == [" shipper"]


def test_values_are_never_changed() -> None:
    data = rename(answer(), "currency", " currency")
    repaired, _ = repair_keys(data, CompactInvoice)
    assert repaired["currency"] == "USD"
    assert repaired["line_items"] == data["line_items"]


def test_raw_format_line_item_keys_are_repaired() -> None:
    line = dict(zip(LINE_COLUMNS, ["Tyres", "2", "PCS", "10.50", "21.00", None], strict=True))
    line["unit_prce"] = line.pop("unit_price")
    data = answer(line_items=[line])
    repaired, repairs = repair_keys(data, RawInvoice)
    assert repaired["line_items"][0]["unit_price"] == "10.50"
    assert repairs.repaired == ["line_items[0]: 'unit_prce' -> 'unit_price'"]
    RawInvoice.model_validate(repaired)


def test_repaired_object_keeps_schema_order() -> None:
    data = rename(answer(), "invoice_number", "Invoice_Number")
    repaired, _ = repair_keys(data, CompactInvoice)
    assert list(repaired) == list(CompactInvoice.model_fields)


@pytest.mark.parametrize("value", [None, "text", 3, ["a"]])
def test_non_objects_pass_through(value: object) -> None:
    assert repair_keys(value, CompactInvoice) == (value, NO_REPAIRS)


def test_allowed_keys_text_lists_every_key_from_the_schema() -> None:
    text = describe_allowed_keys(CompactInvoice)
    for key in CompactInvoice.model_fields:
        assert key in text
    for key in RawParty.model_fields:
        assert key in text
    assert ", ".join(LINE_COLUMNS) in text
