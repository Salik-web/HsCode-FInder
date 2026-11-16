"""Fixed values shared by every schema."""

from typing import Literal, get_args

SchemaVersion = Literal["1.0"]
SCHEMA_VERSION: SchemaVersion = "1.0"

Incoterm = Literal["EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP"]
INCOTERMS: tuple[str, ...] = get_args(Incoterm)

# Units of measure the system accepts. NOS ("numbers") is its own unit, not a synonym of
# PCS (Salik, 2026-11-12). To be aligned with Pakistan Customs/WeBOC unit codes.
Unit = Literal["PCS", "NOS", "SET", "PAIR", "CTN", "BAG", "KG", "MTR", "MT"]
UNITS: tuple[str, ...] = get_args(Unit)

# Critical fields: a document counts as right only if all of them are. Every field that
# isn't per line (party names included: a wrong buyer or consignee is a serious customs
# error, Salik 2026-11-16), plus each line's amount.
CRITICAL_HEADER_FIELDS: tuple[str, ...] = (
    "invoice_number",
    "total_amount",
    "currency",
    "incoterm",
    "buyer.name",
    "consignee.name",
)
CRITICAL_LINE_FIELD = "amount"
