"""Validated invoice: every value carries confidence, provenance and a status."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

import pydantic
from pydantic import BaseModel

from app.schemas.constants import SCHEMA_VERSION, Incoterm, SchemaVersion

Confidence = Annotated[float, pydantic.Field(ge=0, le=1)]
FieldStatus = Literal["ok", "needs_review", "failed_check"]


class Field[T](BaseModel):
    value: T | None
    confidence: Confidence
    source_page: int | None
    status: FieldStatus = "ok"


class Party(BaseModel):
    name: Field[str]
    address: Field[str]
    country: Field[str]  # ISO-3166 alpha-2


class HSSuggestion(BaseModel):
    code: str
    reason: str
    confidence: Confidence


class LineItem(BaseModel):
    description: Field[str]
    quantity: Field[Decimal]
    unit: Field[str]
    unit_price: Field[Decimal]
    amount: Field[Decimal]
    hs_code_on_doc: Field[str] | None
    hs_suggestions: list[HSSuggestion] = []


class Invoice(BaseModel):
    schema_version: SchemaVersion = SCHEMA_VERSION
    invoice_number: Field[str]
    invoice_date: Field[date]
    shipper: Party
    consignee: Party
    incoterm: Field[Incoterm]
    currency: Field[str]  # ISO-4217
    line_items: list[LineItem]
    total_amount: Field[Decimal]
    gross_weight_kg: Field[Decimal] | None
    net_weight_kg: Field[Decimal] | None
