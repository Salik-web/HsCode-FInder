"""Deterministic rules applied to a validated invoice. The LLM proposes, code verifies.

- Reference buyers: a buyer block that says "Same as consignee" names no company. When
  the model copies that phrase as the buyer's name, the buyer is the consignee: copy it,
  and record that a rule did it.
- Units: only a fixed list is accepted. Exact, well-known synonyms map to it
  ("PIECES" -> "PCS"). Anything else keeps its value and is marked failed_check: a
  near-miss like "MET" could be MT or MTR, and guessing would hide the error.
"""

import re
import unicodedata

from pydantic import BaseModel

from app.schemas.constants import UNITS
from app.schemas.raw import RawInvoice, RawLineItem
from app.schemas.result import FieldCheck

# Whole-phrase match only, after lower-casing and dropping punctuation, so a company
# called "Consignee Logistics" or "As Per Trading" never matches.
REFERENCE_PHRASE = re.compile(
    r"^(?:(?:same|ditto)\s+as|as\s+per|as)\s+(?:the\s+)?"
    r"(?:consignee|ship\s*to|deliver(?:y)?\s*to|consigned\s*to|delivery\s+address)"
    r"(?:\s+(?:details|address|party))?$"
)
NON_WORD = re.compile(r"[^\w\s]+")

# Spellings with one accepted meaning in trade documents. Each must map to exactly one
# unit; anything not listed is a failed check, never a guess.
UNIT_SYNONYMS: dict[str, str] = {
    "PC": "PCS",
    "PCE": "PCS",
    "PCES": "PCS",
    "PIECE": "PCS",
    "PIECES": "PCS",
    "SETS": "SET",
    "PAIRS": "PAIR",
    "PR": "PAIR",
    "PRS": "PAIR",
    "CTNS": "CTN",
    "CARTON": "CTN",
    "CARTONS": "CTN",
    "BAGS": "BAG",
    "KGS": "KG",
    "KILOGRAM": "KG",
    "KILOGRAMS": "KG",
    "MTRS": "MTR",
    "METER": "MTR",
    "METERS": "MTR",
    "METRE": "MTR",
    "METRES": "MTR",
    "METRIC TON": "MT",
    "METRIC TONS": "MT",
    "METRIC TONNE": "MT",
    "METRIC TONNES": "MT",
    "TONNE": "MT",
    "TONNES": "MT",
}
REFERENCE_RULE = "buyer resolved_by_rule: the buyer block refers to the consignee"
UNIT_RULE = "unit_list"


class RuleOutcome(BaseModel):
    invoice: RawInvoice
    applied: list[str]  # changes rules made, e.g. REFERENCE_RULE, "line_items[0].unit -> PCS"
    checks: list[FieldCheck]


def apply_rules(invoice: RawInvoice) -> RuleOutcome:
    invoice, applied = resolve_buyer_reference(invoice)
    lines: list[RawLineItem] = []
    checks: list[FieldCheck] = []
    for index, line in enumerate(invoice.line_items):
        unit, change, check = check_unit(line.unit, f"line_items[{index}].unit")
        lines.append(line.model_copy(update={"unit": unit}))
        applied += [change] if change else []
        checks += [check] if check else []
    return RuleOutcome(
        invoice=invoice.model_copy(update={"line_items": lines}), applied=applied, checks=checks
    )


def is_reference_phrase(text: str | None) -> bool:
    if text is None:
        return False
    folded = unicodedata.normalize("NFKC", text).casefold()
    return bool(REFERENCE_PHRASE.match(" ".join(NON_WORD.sub(" ", folded).split())))


def resolve_buyer_reference(invoice: RawInvoice) -> tuple[RawInvoice, list[str]]:
    """Copy the consignee into the buyer when the buyer's name is a reference phrase."""
    if not is_reference_phrase(invoice.buyer.name) or invoice.consignee.name is None:
        return invoice, []
    return invoice.model_copy(update={"buyer": invoice.consignee.model_copy()}), [REFERENCE_RULE]


def check_unit(unit: str | None, path: str) -> tuple[str | None, str | None, FieldCheck | None]:
    """Return the unit to keep, the change made (if any) and a failed check (if any)."""
    if unit is None:
        return None, None, None
    key = " ".join(unit.strip().upper().rstrip(".").split())
    if key in UNITS:
        change = None if key == unit else f"{path}: {unit!r} -> {key!r}"
        return key, change, None
    if key in UNIT_SYNONYMS:
        canonical = UNIT_SYNONYMS[key]
        return canonical, f"{path}: {unit!r} -> {canonical!r}", None
    check = FieldCheck(
        path=path,
        status="failed_check",
        rule=UNIT_RULE,
        detail=f"{unit!r} is not a known unit or an exact synonym of one",
    )
    return unit, None, check
