"""Plain-value invoice: what the LLM fills in, and the shape of synthetic ground truth.

Every key is required but nullable, so "not on the document" is an explicit None and
never a missing key. Confidence and status are computed later in code, not by the model.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.schemas.constants import Incoterm


class RawModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RawParty(RawModel):
    name: str | None
    address: str | None
    country: str | None  # ISO-3166 alpha-2


class RawLineItem(RawModel):
    description: str | None
    quantity: Decimal | None
    unit: str | None
    unit_price: Decimal | None
    amount: Decimal | None
    hs_code_on_doc: str | None


class RawInvoice(RawModel):
    invoice_number: str | None
    invoice_date: date | None
    shipper: RawParty
    buyer: RawParty  # sold-to / bill-to: who pays; often, not always, the consignee
    consignee: RawParty  # ship-to / deliver-to: who receives the goods
    notify_party: RawParty | None  # None when the document names no notify party
    incoterm: Incoterm | None
    currency: str | None  # ISO-4217
    line_items: list[RawLineItem]
    total_amount: Decimal | None
    gross_weight_kg: Decimal | None
    net_weight_kg: Decimal | None
