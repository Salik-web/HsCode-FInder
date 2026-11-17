"""Deterministic guardrails on a validated invoice (DESIGN.md §5.4). LLM proposes, code verifies.

A check that fails returns a FieldCheck on the field it doubts: the value is kept, marked
failed_check, with the reason, and a person decides. A check whose inputs are missing
(None: not on the document) doesn't run, because absence isn't evidence of an error.

The Incoterm is also a closed list in the extraction schema, so an LLM answer outside it
never validates; checking again here keeps the guarantee for any RawInvoice, including
ones built with model_copy, which skips validation. The HS-code existence check needs
the tariff table and arrives in Phase 5.

The party boundary check needs the text layer (Salik, 2026-11-17): where the page prints
a party's name followed by its address, the extracted name and address must cover that
text between them. It flags both fields, never moves a word, when
- the address starts before the name ends: a word is in both;
- the boundary falls inside a word ("Graf, Nina" + "Albers-Straße" on
  "Graf, Nina-Albers-Straße"): a word is split between them;
- on the same text line, words between them belong to neither.
Across a line break, words in between are allowed: PDFium joins side-by-side columns into
one text line. One printing that passes is enough, so a short name that also occurs
inside an address can't raise a false alarm. Without a text layer it doesn't run.
"""

import re
import string
import unicodedata
from datetime import date
from decimal import Decimal

from app.pipeline.rules import RuleOutcome, apply_rules
from app.schemas.constants import INCOTERMS
from app.schemas.iso_codes import COUNTRIES, CURRENCIES
from app.schemas.raw import RawInvoice, RawLineItem
from app.schemas.result import FieldCheck

LINE_MATH_RULE = "line_math"
TOTAL_RULE = "invoice_total"
WEIGHT_RULE = "net_le_gross"
INCOTERM_RULE = "incoterm_list"
CURRENCY_RULE = "iso_currency"
COUNTRY_RULE = "iso_country"
DATE_RULE = "date_sanity"
PARTY_BOUNDARY_RULE = "party_boundary"
RULES = (
    LINE_MATH_RULE,
    TOTAL_RULE,
    WEIGHT_RULE,
    INCOTERM_RULE,
    CURRENCY_RULE,
    COUNTRY_RULE,
    DATE_RULE,
    PARTY_BOUNDARY_RULE,
)

# quantity x unit price may differ from the amount by at most a cent (inclusive): the
# document rounds the product to cents, which moves it by half a cent at most.
LINE_MATH_TOLERANCE = Decimal("0.01")
# Sum of line amounts vs the total: rounding only. Each line amount and the total are
# rounded to cents, which moves the sum by at most half a cent each. Anything more is an
# error, or a charge or discount between subtotal and total, which extraction doesn't
# capture until prompt v4 (Salik, 2026-11-16; see CLAUDE.md).
HALF_CENT = Decimal("0.005")
MAX_INVOICE_AGE_YEARS = 2
PARTIES = ("shipper", "buyer", "consignee", "notify_party")
# Between the words of a printed value: spacing, and separators that a page or an answer
# may add, drop or swap ("Kapadia Road Jalandhar" vs "Kapadia Road, Jalandhar"; a layout
# printing "name | address"). Not "/" or "-": they belong to values ("205/55",
# "Bhubaneswar-770913").
SEPARATORS = ",;|·"
VALUE_WORD = re.compile(rf"[^\s{SEPARATORS}]+")
WORD_GAP = rf"[\s{SEPARATORS}]+"
WORD_END = re.compile(r"\w-?\Z")  # \Z, not $: $ also matches before a final line break
WORD_START = re.compile(r"-?\w")


def verify_invoice(
    invoice: RawInvoice, as_of: date, *, text_layer: str | None = None
) -> RuleOutcome:
    """Every deterministic step after extraction: the rules, then the checks on their result.

    `text_layer` is None without one (a photo), or where there is no page (eval ground truth).
    """
    outcome = apply_rules(invoice)
    found = validate_invoice(outcome.invoice, as_of, text_layer=text_layer)
    return outcome.model_copy(update={"checks": [*outcome.checks, *found]})


def validate_invoice(
    invoice: RawInvoice, as_of: date, *, text_layer: str | None = None
) -> list[FieldCheck]:
    """Every failed check. `as_of` is the day dates are judged against (today, in the API)."""
    checks: list[FieldCheck] = []
    if text_layer is not None:
        checks += check_party_boundaries(invoice, text_layer)
    for index, line in enumerate(invoice.line_items):
        checks += check_line_math(line, index)
    checks += check_total(invoice)
    checks += check_weights(invoice.net_weight_kg, invoice.gross_weight_kg)
    checks += check_incoterm(invoice.incoterm)
    checks += check_currency(invoice.currency)
    checks += check_countries(invoice)
    checks += check_date(invoice.invoice_date, as_of)
    return checks


def check_line_math(line: RawLineItem, index: int) -> list[FieldCheck]:
    if line.quantity is None or line.unit_price is None or line.amount is None:
        return []
    product = line.quantity * line.unit_price
    difference = abs(product - line.amount)
    if difference <= LINE_MATH_TOLERANCE:
        return []
    detail = (
        f"quantity {line.quantity} x unit price {line.unit_price} = {product}, "
        f"but the amount is {line.amount} (off by {difference})"
    )
    return [_failed(f"line_items[{index}].amount", LINE_MATH_RULE, detail)]


def check_total(invoice: RawInvoice) -> list[FieldCheck]:
    """Skipped without lines or with any amount missing: there is no sum to compare."""
    amounts = [line.amount for line in invoice.line_items]
    if invoice.total_amount is None or not amounts or None in amounts:
        return []
    line_sum = sum((amount for amount in amounts if amount is not None), Decimal(0))
    difference = abs(line_sum - invoice.total_amount)
    tolerance = total_tolerance(len(amounts))
    if difference <= tolerance:
        return []
    detail = (
        f"line amounts add up to {line_sum}, but the total is {invoice.total_amount} "
        f"(off by {difference}; rounding allows {tolerance})"
    )
    return [_failed("total_amount", TOTAL_RULE, detail)]


def total_tolerance(line_count: int) -> Decimal:
    """Half a cent per rounded line amount, plus half a cent for the rounded total."""
    return HALF_CENT * (line_count + 1)


def check_weights(net: Decimal | None, gross: Decimal | None) -> list[FieldCheck]:
    """Net above gross can't both be right, and either could be the misread one: flag both."""
    if net is None or gross is None or net <= gross:
        return []
    detail = f"net weight {net} kg is more than gross weight {gross} kg"
    return [
        _failed("net_weight_kg", WEIGHT_RULE, detail),
        _failed("gross_weight_kg", WEIGHT_RULE, detail),
    ]


def check_incoterm(incoterm: str | None) -> list[FieldCheck]:
    if incoterm is None or incoterm in INCOTERMS:
        return []
    return [_failed("incoterm", INCOTERM_RULE, f"{incoterm!r} is not an Incoterms 2020 rule")]


def check_currency(currency: str | None) -> list[FieldCheck]:
    """Case doesn't matter ("usd" names one currency); anything else must be exact."""
    if currency is None or currency.strip().upper() in CURRENCIES:
        return []
    return [_failed("currency", CURRENCY_RULE, f"{currency!r} is not an ISO 4217 currency")]


def check_countries(invoice: RawInvoice) -> list[FieldCheck]:
    checks: list[FieldCheck] = []
    for name in PARTIES:
        party = getattr(invoice, name)
        if party is None or party.country is None:
            continue
        if party.country.strip().upper() not in COUNTRIES:
            detail = f"{party.country!r} is not an ISO 3166-1 alpha-2 country code"
            checks.append(_failed(f"{name}.country", COUNTRY_RULE, detail))
    return checks


def check_date(invoice_date: date | None, as_of: date) -> list[FieldCheck]:
    if invoice_date is None:
        return []
    if invoice_date > as_of:
        return [_failed("invoice_date", DATE_RULE, f"{invoice_date} is after {as_of}")]
    oldest = years_before(as_of, MAX_INVOICE_AGE_YEARS)
    if invoice_date < oldest:
        detail = f"{invoice_date} is more than {MAX_INVOICE_AGE_YEARS} years before {as_of}"
        return [_failed("invoice_date", DATE_RULE, detail)]
    return []


def check_party_boundaries(invoice: RawInvoice, text_layer: str) -> list[FieldCheck]:
    """Flag a party whose name and address split the printed text wrongly (module docstring)."""
    text = unicodedata.normalize("NFKC", text_layer)
    checks: list[FieldCheck] = []
    for party_name in PARTIES:
        party = getattr(invoice, party_name)
        if party is None or party.name is None or party.address is None:
            continue
        problem = boundary_problem(party.name, party.address, text)
        if problem is None:
            continue
        detail = f"name {party.name!r} and address {party.address!r}: {problem}"
        checks.append(_failed(f"{party_name}.name", PARTY_BOUNDARY_RULE, detail))
        checks.append(_failed(f"{party_name}.address", PARTY_BOUNDARY_RULE, detail))
    return checks


def boundary_problem(name: str, address: str, text: str) -> str | None:
    """What is wrong where the name is printed followed by the address; None if nothing is,
    if one printing is right, or if the page never prints the address after the name."""
    address_pattern = _printed(address)
    problems = []
    for name_match in _printed(name).finditer(text):
        address_match = address_pattern.search(text, name_match.start() + 1)
        if address_match is None:
            continue
        problem = _join_problem(text, name_match.end(), address_match.start())
        if problem is None:
            return None
        problems.append(problem)
    return problems[0] if problems else None


def _printed(value: str) -> re.Pattern[str]:
    """The value as the page may print it: same words, any spacing, commas or semicolons.

    It must start a word; where it ends is what the boundary check examines."""
    words = VALUE_WORD.findall(unicodedata.normalize("NFKC", value))
    return re.compile(r"(?<!\w)" + WORD_GAP.join(map(re.escape, words)), re.IGNORECASE)


def _join_problem(text: str, name_end: int, address_start: int) -> str | None:
    if address_start < name_end:
        return "a word is in both the name and the address"
    if _splits_word(text, name_end) or _splits_word(text, address_start):
        return "the boundary between them splits a word"
    between = text[name_end:address_start]
    if "\n" not in between and any(char.isalnum() for char in between):
        leftover = between.strip(f"{string.whitespace}{SEPARATORS}")
        return f"{leftover!r} is printed between them but belongs to neither"
    return None


def _splits_word(text: str, position: int) -> bool:
    """A word, hyphenated words included, continues on both sides of `position`."""
    before, after = text[:position], text[position:]
    if before.endswith("-") and after.startswith("-"):
        return False
    return bool(WORD_END.search(before) and WORD_START.match(after))


def years_before(day: date, years: int) -> date:
    """The same day `years` earlier; 29 February becomes 28 February in a common year."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def _failed(path: str, rule: str, detail: str) -> FieldCheck:
    return FieldCheck(path=path, status="failed_check", rule=rule, detail=detail)
