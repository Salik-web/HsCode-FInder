"""Fixed values shared by every schema."""

from typing import Literal, get_args

SchemaVersion = Literal["1.0"]
SCHEMA_VERSION: SchemaVersion = "1.0"

Incoterm = Literal["EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP"]
INCOTERMS: tuple[str, ...] = get_args(Incoterm)

# Units of measure the system accepts. Synthetic invoices print only these.
Unit = Literal["PCS", "SET", "PAIR", "CTN", "BAG", "KG", "MTR", "MT"]
UNITS: tuple[str, ...] = get_args(Unit)
