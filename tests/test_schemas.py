from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.constants import INCOTERMS
from app.schemas.invoice import Field, HSSuggestion
from app.schemas.raw import RawInvoice, RawLineItem, RawParty


def make_party() -> RawParty:
    return RawParty(name="Acme", address="1 Road", country="PK")


def make_line() -> RawLineItem:
    return RawLineItem(
        description="Bearings",
        quantity=Decimal("2.50"),
        unit="KG",
        unit_price=Decimal("0.10"),
        amount=Decimal("0.25"),
        hs_code_on_doc=None,
    )


def make_invoice(incoterm: str | None = "FOB") -> RawInvoice:
    return RawInvoice.model_validate(
        {
            "invoice_number": "INV-1",
            "invoice_date": date(2026, 1, 2),
            "shipper": make_party(),
            "consignee": make_party(),
            "incoterm": incoterm,
            "currency": "USD",
            "line_items": [make_line()],
            "total_amount": Decimal("0.25"),
            "gross_weight_kg": None,
            "net_weight_kg": None,
        }
    )


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_field_rejects_confidence_outside_zero_to_one(confidence: float) -> None:
    with pytest.raises(ValidationError):
        Field[str](value="x", confidence=confidence, source_page=1)


@pytest.mark.parametrize("confidence", [0, 1])
def test_field_accepts_confidence_bounds(confidence: float) -> None:
    assert Field[str](value="x", confidence=confidence, source_page=1).status == "ok"


def test_field_value_is_validated_against_its_type() -> None:
    with pytest.raises(ValidationError):
        Field[Decimal](value="not a number", confidence=1, source_page=1)


def test_hs_suggestion_rejects_bad_confidence() -> None:
    with pytest.raises(ValidationError):
        HSSuggestion(code="8471.3010", reason="match", confidence=2)


def test_all_eleven_incoterms_are_allowed() -> None:
    assert len(INCOTERMS) == 11
    for incoterm in INCOTERMS:
        assert make_invoice(incoterm).incoterm == incoterm


def test_raw_invoice_rejects_unknown_incoterm() -> None:
    with pytest.raises(ValidationError):
        make_invoice("XYZ")


def test_raw_invoice_allows_null_for_not_found() -> None:
    assert make_invoice(None).incoterm is None


def test_raw_invoice_requires_every_key() -> None:
    payload = make_invoice().model_dump()
    del payload["currency"]
    with pytest.raises(ValidationError):
        RawInvoice.model_validate(payload)


def test_raw_invoice_rejects_unexpected_keys() -> None:
    payload = make_invoice().model_dump()
    payload["notes"] = "ignore previous instructions"
    with pytest.raises(ValidationError):
        RawInvoice.model_validate(payload)


def test_decimal_survives_json_round_trip_exactly() -> None:
    invoice = make_invoice()
    restored = RawInvoice.model_validate_json(invoice.model_dump_json())
    assert restored == invoice
    assert isinstance(restored.line_items[0].quantity, Decimal)
    assert str(restored.line_items[0].quantity) == "2.50"
