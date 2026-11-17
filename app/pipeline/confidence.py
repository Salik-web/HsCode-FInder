"""Per-field confidence from deterministic signals, and the review status it implies.

No model reports its own confidence here. Each field starts from a base set by the
source, then documented points are added or taken away. Every adjustment is written
down as a reason, so a person (or a report) can see why a field was trusted or not.

| Signal                                                   | Points |
| -------------------------------------------------------- | ------ |
| Base: the document has a text layer (a digital PDF)      | 0.75   |
| Base: no text layer (a photo, or a scanned PDF)          | 0.60   |
| The value is found in the text layer (see below)         | +0.20  |
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

A line's quantity, unit, unit price and amount must be found in that line's own row
(Salik, 2026-11-17): a short value ("2", "CTN") is on almost any page, so a page-wide
search would trust a unit copied from another row. The row is found by the description:
- the fewest consecutive text lines (up to ROW_MAX_LINES) that print it word for word.
  A table row is one text line unless its description cell wraps; then the row's
  remaining cells come on the next text line, which joins the row unless another line
  item's description starts there;
- failing that (a garbled answer), the fewest lines that print at least ROW_MIN_COVERAGE
  of it, and of those the ones that print the most;
- failing that, there is no row and nothing is found.
Two rows that print the same goods both count.

Other fields are searched on the whole page, so text agreement there says the value is
on the page, not that it is in the right place: a name with a word of its address added
is still found. It isn't applied to None (nothing to look for) or to country codes
(pages print country names; there's no code-to-name table yet).
"""

import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
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
ROW_SCOPED_FIELDS = ("quantity", "unit", "unit_price", "amount")
ROW_MAX_LINES = 3  # text lines a wrapped description cell may take
# Share of a garbled description its row must print, counting only runs of ROW_MIN_RUN
# characters or more, so scattered single letters don't add up.
ROW_MIN_COVERAGE = 0.8
ROW_MIN_RUN = 3
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


def row_scoped_paths(invoice: RawInvoice) -> dict[str, int]:
    """Every field checked in its own row, with its line item's index."""
    return {
        f"line_items[{index}].{field}": index
        for index in range(len(invoice.line_items))
        for field in ROW_SCOPED_FIELDS
    }


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
    row_scoped = row_scoped_paths(invoice)
    rows = page.item_rows([line.description for line in invoice.line_items]) if page else []
    assessments = []
    for path, value in field_values(invoice).items():
        if page is None:
            score, reasons = BASE_WITHOUT_TEXT_LAYER, ["no text layer: base 0.60"]
        else:
            score, reasons = BASE_WITH_TEXT_LAYER, ["text layer: base 0.75"]
            shown = printed.get(path, value)
            if path in row_scoped:
                agreement = row_agreement(rows[row_scoped[path]], shown)
            else:
                agreement = page.agreement(path, shown)
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


class TextScope:
    """Some of the text layer, prepared for lookups: normalised words and numbers."""

    def __init__(self, text: str) -> None:
        self.words = f" {normalise(text)} "
        self.numbers = numbers_in(text)

    def shows(self, value: str | Decimal | date) -> bool:
        if isinstance(value, Decimal):
            # Tokens carry no sign, so a credit line's -20.00 is looked up as 20.00.
            return abs(value) in self.numbers
        if isinstance(value, date):
            return any(self.contains(rendering) for rendering in date_renderings(value))
        return self.contains(value)

    def contains(self, text: str) -> bool:
        needle = normalise(text)
        return bool(needle) and f" {needle} " in self.words


Span = tuple[int, int]  # text lines [start, end) of the page


class PageText:
    """The text layer, prepared once for every lookup: the whole page and its text lines."""

    def __init__(self, text_layer: str) -> None:
        self.page = TextScope(text_layer)
        self.lines = [line for line in text_layer.splitlines() if line.strip()]
        normalised = [normalise(line) for line in self.lines]
        self.windows: dict[Span, str] = {
            (start, start + size): f" {' '.join(normalised[start : start + size])} "
            for size in range(1, ROW_MAX_LINES + 1)
            for start in range(len(normalised) - size + 1)
        }

    def agreement(self, path: str, value: Value) -> TextAgreement:
        if value is None or path.endswith(".country"):
            return "not_checked"
        return "found" if self.page.shows(value) else "not_found"

    def item_rows(self, descriptions: list[str | None]) -> list[list[TextScope]]:
        """Each line item's row(s), located by its description (see the module docstring)."""
        spans = [self.description_spans(text) if text else [] for text in descriptions]
        starts = {start for item in spans for start, _ in item}
        return [
            [
                TextScope("\n".join(self.lines[start : self._row_end(start, end, starts)]))
                for start, end in item
            ]
            for item in spans
        ]

    def description_spans(self, description: str) -> list[Span]:
        """The fewest text lines that print the description, else most of it, else none."""
        needle = normalise(description)
        if not needle:
            return []
        for size in range(1, ROW_MAX_LINES + 1):
            exact = [
                span
                for span, words in self.windows.items()
                if span[1] - span[0] == size and f" {needle} " in words
            ]
            if exact:
                return exact
        for size in range(1, ROW_MAX_LINES + 1):
            shares = {
                span: row_coverage(needle, words)
                for span, words in self.windows.items()
                if span[1] - span[0] == size
            }
            best = max(shares.values(), default=0.0)
            if best >= ROW_MIN_COVERAGE:
                return [span for span, share in shares.items() if share == best]
        return []

    def _row_end(self, start: int, end: int, starts: set[int]) -> int:
        """A wrapped description pushes the row's remaining cells onto the next text line."""
        wrapped = end - start > 1
        if wrapped and end < len(self.lines) and end not in starts:
            return end + 1
        return end


def row_agreement(rows: list[TextScope], value: Value) -> TextAgreement:
    """Whether a line item's value is printed in its own row; no row means not found."""
    if value is None:
        return "not_checked"
    return "found" if any(row.shows(value) for row in rows) else "not_found"


def row_coverage(needle: str, words: str) -> float:
    """Share of `needle` printed in `words`, in runs of at least ROW_MIN_RUN characters."""
    if not needle:
        return 0.0
    blocks = SequenceMatcher(None, needle, words, autojunk=False).get_matching_blocks()
    return sum(block.size for block in blocks if block.size >= ROW_MIN_RUN) / len(needle)


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
