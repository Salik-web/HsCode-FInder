from app.schemas.compact import LINE_COLUMNS, CompactInvoice
from app.schemas.raw import RawInvoice, RawLineItem
from synth.generator import generate_ground_truth, load_catalogue
from tests.test_extract import compact_json


def test_columns_match_the_raw_line_item_fields_in_order() -> None:
    assert tuple(RawLineItem.model_fields) == LINE_COLUMNS


def test_every_other_field_is_raw_invoice_unchanged() -> None:
    assert list(CompactInvoice.model_fields) == list(RawInvoice.model_fields)
    raw = RawInvoice.model_json_schema(mode="serialization")["properties"]
    compact = CompactInvoice.model_json_schema(mode="serialization")["properties"]
    assert {k: v for k, v in raw.items() if k != "line_items"} == {
        k: v for k, v in compact.items() if k != "line_items"
    }


def test_schema_states_the_column_order_once() -> None:
    line_items = CompactInvoice.model_json_schema(mode="serialization")["properties"]["line_items"]
    assert ", ".join(LINE_COLUMNS) in line_items["description"]
    row = line_items["items"]
    assert row["minItems"] == row["maxItems"] == len(LINE_COLUMNS)
    assert len(row["prefixItems"]) == len(LINE_COLUMNS)


def test_round_trip_through_compact_is_lossless() -> None:
    catalogue = load_catalogue()
    for index in range(40):
        truth = generate_ground_truth(42, index, catalogue).invoice
        assert CompactInvoice.model_validate_json(compact_json(truth)).to_raw() == truth


def test_empty_line_items_convert() -> None:
    truth = generate_ground_truth(42, 0, load_catalogue()).invoice.model_copy(
        update={"line_items": []}
    )
    assert CompactInvoice.model_validate_json(compact_json(truth)).to_raw() == truth
