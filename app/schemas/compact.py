"""Compact LLM output: line items as fixed-order arrays instead of keyed objects.

Repeating six key names on every line roughly doubles the output, and some free tiers
cap output at 1,000 tokens a minute. Header and party fields stay named. The column
order is defined once, here, and the result is converted to RawInvoice in code.
"""

from decimal import Decimal
from typing import Annotated

from pydantic import Field

from app.schemas.raw import RawInvoice, RawLineItem

LINE_COLUMNS: tuple[str, ...] = (
    "description",
    "quantity",
    "unit",
    "unit_price",
    "amount",
    "hs_code_on_doc",
)
# Positionally typed, so Pydantic checks both the length and each column's type.
CompactLine = tuple[
    str | None, Decimal | None, str | None, Decimal | None, Decimal | None, str | None
]
LINE_ITEMS_DESCRIPTION = "One array per goods line, columns in this order: " + ", ".join(
    LINE_COLUMNS
)


class CompactInvoice(RawInvoice):
    """RawInvoice with line items as arrays; every other field is inherited unchanged."""

    line_items: Annotated[  # type: ignore[assignment]  # deliberate narrowing override
        list[CompactLine], Field(description=LINE_ITEMS_DESCRIPTION)
    ]

    def to_raw(self) -> RawInvoice:
        lines = [
            RawLineItem(**dict(zip(LINE_COLUMNS, row, strict=True))) for row in self.line_items
        ]
        header = {name: getattr(self, name) for name in RawInvoice.model_fields}
        return RawInvoice.model_validate(header | {"line_items": lines})
