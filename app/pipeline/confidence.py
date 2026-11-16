"""Per-field confidence from deterministic signals, and the review status it implies.

No model reports its own confidence here. Each field starts from a base set by the
source, then documented points are added or taken away. Every adjustment is written
down as a reason, so a person (or a report) can see why a field was trusted or not.

| Signal                                                   | Points |
| -------------------------------------------------------- | ------ |
| Base: the document has a text layer (a digital PDF)      | 0.75   |
| Base: no text layer (a photo, or a scanned PDF)          | 0.60   |
| The value is found in the text layer                     | +0.20  |
| The value is not found in the text layer                 | -0.45  |
| The answer needed a retry                                | -0.10  |
| Key names in the answer were repaired                    | -0.05  |
| This number's format was reinterpreted                   | -0.10  |
| A rule set or changed this value                         | -0.05  |
| A guardrail check failed on this field                   | cap at 0.10, status failed_check |

Below REVIEW_THRESHOLD, a field's status is needs_review. Without a text layer, nothing
can verify a value, so critical fields always go to needs_review ("no text layer to
verify", Salik 2026-11-16); their score is left as the evidence gives it, because the
status is a policy, not a measurement. Non-critical fields keep the threshold.

Text agreement says the value is on the page, not that it is in the right place: a short
value ("2", "PCS") is found almost anywhere, and a name cut short is still found. It
isn't applied to None (nothing to look for) or to country codes (pages print country
names; there's no code-to-name table yet).
"""

import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.pipeline.numbers import read_number
from app.pipeline.rules import REFERENCE_RULE, parse_change
from app.schemas.constants import CRITICAL_HEADER_FIELDS, CRITICAL_LINE_FIELD
from app.schemas.invoice import FieldStatus
from app.schemas.raw import RawInvoice
from app.schemas.result import FieldAssessment, FieldCheck, LLMCall

BASE_WITH_TEXT_LAYER = 0.75
BASE_WITHOUT_TEXT_LAYER = 0.60
FOUND_IN_TEXT = 0.20
NOT_FOUND_IN_TEXT = -0.45
RETRIED = -0.10
KEYS_REPAIRED = -0.05
NUMBER_REFORMATTED = -0.10
SET_BY_RULE = -0.05
FAILED_CHECK_CAP = 0.10
REVIEW_THRESHOLD = 0.50
NO_TEXT_LAYER_TO_VERIFY = "no text layer to verify"

HEADER_FIELDS = (
    "invoice_number",
    "invoice_date",
    "incoterm",
    "currency",
    "total_amount",
    "gross_weight_kg",
    "net_weight_kg",
)
PARTIES = ("shipper", "buyer", "consignee", "notify_party")
PARTY_FIELDS = ("name", "address", "country")
LINE_FIELDS = ("description", "quantity", "unit", "unit_price", "amount", "hs_code_on_doc")
# English month names, written out: the calendar module's names follow the locale.
MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)

Value = str | Decimal | date | None
TextAgreement = Literal["found", "not_found", "not_checked"]


def field_values(invoice: RawInvoice) -> dict[str, Value]:
    """Every assessed field by its pipeline path; an absent party's fields are None."""
    values: dict[str, Value] = {name: getattr(invoice, name) for name in HEADER_FIELDS}
    for party_name in PARTIES:
        party = getattr(invoice, party_name)
        for field in PARTY_FIELDS:
            values[f"{party_name}.{field}"] = None if party is None else getattr(party, field)
    for index, line in enumerate(invoice.line_items):
        for field in LINE_FIELDS:
            values[f"line_items[{index}].{field}"] = getattr(line, field)
    return values


def assess_fields(
    invoice: RawInvoice,
    text_layer: str | None,
    calls: list[LLMCall],
    rules_applied: list[str],
    checks: list[FieldCheck],
) -> list[FieldAssessment]:
    """Score every field of a validated invoice. `calls` are the extraction's attempts."""
    page = PageText(text_layer) if text_layer else None
    answer = calls[-1] if calls else None
    printed = {change.path: change.printed for c in rules_applied if (change := parse_change(c))}
    set_by_rule = set(printed)
    if REFERENCE_RULE in rules_applied:
        set_by_rule |= {f"buyer.{field}" for field in PARTY_FIELDS}
    reformatted = set(answer.numbers_normalised) if answer else set()
    failed = {check.path for check in checks}
    assessments = []
    for path, value in field_values(invoice).items():
        if page is None:
            score, reasons = BASE_WITHOUT_TEXT_LAYER, ["no text layer: base 0.60"]
        else:
            score, reasons = BASE_WITH_TEXT_LAYER, ["text layer: base 0.75"]
            agreement = page.agreement(path, printed.get(path, value))
            if agreement == "found":
                score, reasons = score + FOUND_IN_TEXT, [*reasons, "found in text layer +0.20"]
            elif agreement == "not_found":
                score += NOT_FOUND_IN_TEXT
                reasons.append("not found in text layer -0.45")
        if len(calls) > 1:
            score, reasons = score + RETRIED, [*reasons, "answer needed a retry -0.10"]
        if answer is not None and (answer.keys_repaired or answer.keys_ignored):
            score, reasons = score + KEYS_REPAIRED, [*reasons, "key names repaired -0.05"]
        if path in reformatted:
            score += NUMBER_REFORMATTED
            reasons.append("number format reinterpreted -0.10")
        if path in set_by_rule:
            score, reasons = score + SET_BY_RULE, [*reasons, "set by a rule -0.05"]
        status: FieldStatus = "ok"
        if path in failed:
            score, status = min(score, FAILED_CHECK_CAP), "failed_check"
            reasons.append("guardrail check failed: capped at 0.10")
        score = round(min(1.0, max(0.0, score)), 2)
        if status == "ok" and page is None and is_critical(path):
            status = "needs_review"
            reasons.append(NO_TEXT_LAYER_TO_VERIFY)
        if status == "ok" and score < REVIEW_THRESHOLD:
            status = "needs_review"
        assessments.append(
            FieldAssessment(path=path, confidence=score, status=status, reasons=reasons)
        )
    return assessments


def is_critical(path: str) -> bool:
    if path in CRITICAL_HEADER_FIELDS:
        return True
    return path.startswith("line_items[") and path.endswith(f"].{CRITICAL_LINE_FIELD}")


def normalise(text: str) -> str:
    """Case, Unicode form, punctuation and spacing don't change what the page says."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    cleaned = "".join(char if char.isalnum() else " " for char in folded)
    return " ".join(cleaned.split())


class PageText:
    """The text layer, prepared once for every lookup: normalised words and numbers."""

    def __init__(self, text_layer: str) -> None:
        self.words = f" {normalise(text_layer)} "
        self.numbers = numbers_in(text_layer)

    def agreement(self, path: str, value: Value) -> TextAgreement:
        if value is None or path.endswith(".country"):
            return "not_checked"
        if isinstance(value, Decimal):
            # Tokens carry no sign, so a credit line's -20.00 is looked up as 20.00.
            found = abs(value) in self.numbers
        elif isinstance(value, date):
            found = any(self.contains(rendering) for rendering in date_renderings(value))
        else:
            found = self.contains(value)
        return "found" if found else "not_found"

    def contains(self, text: str) -> bool:
        needle = normalise(text)
        return bool(needle) and f" {needle} " in self.words


def numbers_in(text: str) -> set[Decimal]:
    """Every number the page shows, read both ways when its separators are ambiguous."""
    found: set[Decimal] = set()
    for token in _number_tokens(text):
        reading = read_number(token)
        for candidate in (reading.certain, reading.if_dot, reading.if_comma):
            if candidate is None:
                continue
            try:
                found.add(Decimal(candidate))
            except InvalidOperation:
                continue
    return found


def _number_tokens(text: str) -> list[str]:
    """Runs of digits with the separators inside them: "12,129.84", "4-3" gives 4 and 3."""
    tokens, current = [], ""
    for char in text + " ":
        if char.isdigit() or (current and char in ".,'"):
            current += char
            continue
        if current:
            tokens.append(current.rstrip(".,'"))
        current = ""
    return tokens


def date_renderings(day: date) -> list[str]:
    """Common ways an invoice prints a date. Punctuation is ignored when comparing."""
    days = {f"{day.day:02d}", str(day.day)}
    numeric_months = {f"{day.month:02d}", str(day.month)}
    month_names = {MONTHS[day.month - 1], MONTHS[day.month - 1][:3]}
    year = str(day.year)
    renderings = [f"{year} {month} {d}" for month in numeric_months for d in days]
    renderings += [f"{d} {month} {year}" for month in numeric_months for d in days]
    renderings += [f"{month} {d} {year}" for month in numeric_months for d in days]
    renderings += [f"{d} {name} {year}" for name in month_names for d in days]
    renderings += [f"{name} {d} {year}" for name in month_names for d in days]
    return renderings
