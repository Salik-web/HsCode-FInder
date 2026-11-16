"""Deterministic guardrails on a validated invoice (DESIGN.md §5.4). LLM proposes, code verifies.

A check that fails returns a FieldCheck on the field it doubts: the value is kept, marked
failed_check, with the reason, and a person decides. A check whose inputs are missing
(None: not on the document) doesn't run, because absence isn't evidence of an error.

The Incoterm is also a closed list in the extraction schema, so an LLM answer outside it
never validates; checking again here keeps the guarantee for any RawInvoice, including
ones built with model_copy, which skips validation. The HS-code existence check needs
the tariff table and arrives in Phase 5.
"""

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
RULES = (
    LINE_MATH_RULE,
    TOTAL_RULE,
    WEIGHT_RULE,
    INCOTERM_RULE,
    CURRENCY_RULE,
    COUNTRY_RULE,
    DATE_RULE,
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


def verify_invoice(invoice: RawInvoice, as_of: date) -> RuleOutcome:
    """Every deterministic step after extraction: the rules, then the checks on their result."""
    outcome = apply_rules(invoice)
    checks = [*outcome.checks, *validate_invoice(outcome.invoice, as_of)]
    return outcome.model_copy(update={"checks": checks})


def validate_invoice(invoice: RawInvoice, as_of: date) -> list[FieldCheck]:
    """Every failed check. `as_of` is the day dates are judged against (today, in the API)."""
    checks: list[FieldCheck] = []
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


def years_before(day: date, years: int) -> date:
    """The same day `years` earlier; 29 February becomes 28 February in a common year."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def _failed(path: str, rule: str, detail: str) -> FieldCheck:
    return FieldCheck(path=path, status="failed_check", rule=rule, detail=detail)
