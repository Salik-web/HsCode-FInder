"""Seeded synthetic invoices with exact ground truth."""

import csv
import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import cache
from pathlib import Path
from typing import Literal, NamedTuple, Self

from faker import Faker
from pydantic import BaseModel, Field, model_validator

from app.schemas.constants import INCOTERMS
from app.schemas.raw import RawInvoice, RawLineItem, RawParty

CATALOGUE_PATH = Path(__file__).with_name("catalogue.csv")

MIN_LINE_ITEMS = 1
MAX_LINE_ITEMS = 15
HS_PRINTED_PROBABILITY = 0.5

# A fixed anchor, not today's date: "today" would make output depend on when it was run.
ANCHOR_DATE = date(2026, 10, 1)
MAX_INVOICE_AGE_DAYS = 365

# Two-decimal currencies only, so rounding always fits the +/- 0.01 line amount guardrail.
CURRENCIES = ("USD", "EUR", "GBP", "AED", "CNY", "PKR", "SAR", "SGD")

Unit = Literal["PCS", "SET", "PAIR", "CTN", "BAG", "KG", "MTR", "MT"]
# These can be fractional on an invoice; every other unit is counted in whole numbers.
MEASURED_UNITS = ("KG", "MTR", "MT")

CENT = Decimal("0.01")
MIN_UNIT_PRICE_CENTS = 50
MAX_UNIT_PRICE_CENTS = 50_000
MIN_QUANTITY = 1
MAX_QUANTITY = 1_000
# Keeps heavy goods plausible: without it, a line could be 1,000 MT of steel.
MAX_LINE_WEIGHT_KG = 2_000
MIN_PACKAGING_PERCENT = 3
MAX_PACKAGING_PERCENT = 10

CONSIGNEE_IN_PAKISTAN_PROBABILITY = 0.7
SHIPPER_IN_PAKISTAN_PROBABILITY = 0.2
BUYER_IS_CONSIGNEE_PROBABILITY = 0.6
# A different buyer is either local to the consignee or a trader in a third country.
BUYER_IN_CONSIGNEE_COUNTRY_PROBABILITY = 0.5
NOTIFY_PARTY_PROBABILITY = 0.4


class Country(NamedTuple):
    code: str
    name: str
    locale: str
    # Set where Faker's locale only has US-shaped addresses ("Aazimbury, OR 13900").
    cities: tuple[str, ...] = ()


PAKISTAN = Country(
    "PK",
    "Pakistan",
    "en_PK",
    ("Karachi", "Lahore", "Faisalabad", "Sialkot", "Rawalpindi", "Multan", "Peshawar"),
)
# Latin-script locales only, until the templates have a CJK font story.
FOREIGN_COUNTRIES = (
    Country("US", "United States", "en_US"),
    Country("GB", "United Kingdom", "en_GB"),
    Country("DE", "Germany", "de_DE"),
    Country("FR", "France", "fr_FR"),
    Country("IT", "Italy", "it_IT"),
    Country("ES", "Spain", "es_ES"),
    Country("NL", "Netherlands", "nl_NL"),
    Country("TR", "Turkey", "tr_TR", ("Istanbul", "Izmir", "Bursa", "Gaziantep", "Mersin")),
    Country("IN", "India", "en_IN"),
)
ALL_COUNTRIES = (PAKISTAN, *FOREIGN_COUNTRIES)
COUNTRY_NAMES = {country.code: country.name for country in ALL_COUNTRIES}


class CatalogueItem(BaseModel):
    description: str = Field(min_length=1)
    hs_code: str = Field(pattern=r"^\d[\d.]*\d$")
    unit: Unit
    kg_per_unit: Decimal = Field(gt=0)


class GroundTruth(BaseModel):
    """What is printed on a document, plus the HS codes it may not print."""

    invoice: RawInvoice
    true_hs_codes: list[str]

    @model_validator(mode="after")
    def one_hs_code_per_line(self) -> Self:
        if len(self.true_hs_codes) != len(self.invoice.line_items):
            raise ValueError("true_hs_codes must align with line_items")
        return self


def derive_rng(seed: int, *parts: str | int) -> random.Random:
    """Return an RNG for one purpose, so streams don't depend on each other's draw order."""
    return random.Random(":".join(str(part) for part in (seed, *parts)))


def load_catalogue(path: Path = CATALOGUE_PATH) -> list[CatalogueItem]:
    with path.open(newline="", encoding="utf-8") as file:
        return [CatalogueItem.model_validate(row) for row in csv.DictReader(file)]


def generate_ground_truth(seed: int, index: int, catalogue: list[CatalogueItem]) -> GroundTruth:
    """Build document `index`; it depends only on the seed, the index and the catalogue."""
    rng = derive_rng(seed, "invoice", index)
    shipper_country, consignee_country = _pick_trade_lane(rng)
    items = rng.sample(catalogue, _pick_line_count(rng, len(catalogue)))
    print_hs_codes = rng.random() < HS_PRINTED_PROBABILITY
    line_items = [_make_line_item(rng, item, print_hs_codes) for item in items]
    net_weight, gross_weight = _make_weights(rng, line_items, items)
    invoice_date = ANCHOR_DATE - timedelta(days=rng.randint(0, MAX_INVOICE_AGE_DAYS))
    invoice_number = _make_invoice_number(rng, invoice_date)
    shipper = _make_party(rng, shipper_country)
    consignee = _make_party(rng, consignee_country)
    # A separate stream, so adding these parties left every other field unchanged.
    party_rng = derive_rng(seed, "parties", index)
    invoice = RawInvoice(
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        shipper=shipper,
        buyer=_make_buyer(party_rng, shipper_country, consignee_country, consignee),
        consignee=consignee,
        notify_party=_make_notify_party(party_rng, consignee_country),
        incoterm=rng.choice(INCOTERMS),
        currency=rng.choice(CURRENCIES),
        line_items=line_items,
        total_amount=sum((line.amount for line in line_items), Decimal("0.00")),
        gross_weight_kg=gross_weight,
        net_weight_kg=net_weight,
    )
    return GroundTruth(invoice=invoice, true_hs_codes=[item.hs_code for item in items])


def _pick_line_count(rng: random.Random, catalogue_size: int) -> int:
    return min(rng.randint(MIN_LINE_ITEMS, MAX_LINE_ITEMS), catalogue_size)


def _pick_trade_lane(rng: random.Random) -> tuple[Country, Country]:
    """Mostly imports into Pakistan, some exports, a few third-country shipments."""
    roll = rng.random()
    if roll < CONSIGNEE_IN_PAKISTAN_PROBABILITY:
        return rng.choice(FOREIGN_COUNTRIES), PAKISTAN
    if roll < CONSIGNEE_IN_PAKISTAN_PROBABILITY + SHIPPER_IN_PAKISTAN_PROBABILITY:
        return PAKISTAN, rng.choice(FOREIGN_COUNTRIES)
    shipper, consignee = rng.sample(FOREIGN_COUNTRIES, 2)
    return shipper, consignee


@cache
def _faker_for(locale: str) -> Faker:
    return Faker(locale)


def _make_party(rng: random.Random, country: Country) -> RawParty:
    fake = _faker_for(country.locale)
    fake.seed_instance(rng.getrandbits(64))
    return RawParty(
        name=fake.company(),
        address=_make_address(rng, fake, country),
        country=country.code,
    )


def _make_buyer(
    rng: random.Random, shipper_country: Country, consignee_country: Country, consignee: RawParty
) -> RawParty:
    if rng.random() < BUYER_IS_CONSIGNEE_PROBABILITY:
        return consignee.model_copy()
    if rng.random() < BUYER_IN_CONSIGNEE_COUNTRY_PROBABILITY:
        return _make_party(rng, consignee_country)
    third_countries = [c for c in ALL_COUNTRIES if c not in (shipper_country, consignee_country)]
    return _make_party(rng, rng.choice(third_countries))


def _make_notify_party(rng: random.Random, consignee_country: Country) -> RawParty | None:
    """Usually the consignee's clearing agent or bank, so it sits in the destination country."""
    if rng.random() >= NOTIFY_PARTY_PROBABILITY:
        return None
    return _make_party(rng, consignee_country)


def _make_address(rng: random.Random, fake: Faker, country: Country) -> str:
    if not country.cities:
        return " ".join(fake.address().split())
    plot = rng.randint(1, 400)
    block = rng.choice("ABCDEFG")
    postcode = rng.randint(10_000, 99_999)
    return f"Plot {plot}, Block {block}, {rng.choice(country.cities)} {postcode}"


def _make_invoice_number(rng: random.Random, invoice_date: date) -> str:
    short_year = invoice_date.strftime("%y")
    serial = rng.randint(1, 99_999)
    style = rng.randrange(4)
    if style == 0:
        return f"INV-{invoice_date.year}-{serial:05d}"
    if style == 1:
        return f"CI/{short_year}/{serial:05d}"
    if style == 2:
        return f"EXP{short_year}{serial:05d}"
    return f"{rng.randint(100, 999)}-{serial:05d}"


def _cents_to_decimal(cents: int) -> Decimal:
    return (Decimal(cents) / 100).quantize(CENT)


def _make_quantity(rng: random.Random, item: CatalogueItem) -> Decimal:
    weight_limit = int(MAX_LINE_WEIGHT_KG / item.kg_per_unit)
    limit = max(MIN_QUANTITY, min(MAX_QUANTITY, weight_limit))
    if item.unit in MEASURED_UNITS:
        return _cents_to_decimal(rng.randint(MIN_QUANTITY * 100, limit * 100))
    return Decimal(rng.randint(MIN_QUANTITY, limit))


def _make_line_item(rng: random.Random, item: CatalogueItem, print_hs_code: bool) -> RawLineItem:
    quantity = _make_quantity(rng, item)
    unit_price = _cents_to_decimal(rng.randint(MIN_UNIT_PRICE_CENTS, MAX_UNIT_PRICE_CENTS))
    return RawLineItem(
        description=item.description,
        quantity=quantity,
        unit=item.unit,
        unit_price=unit_price,
        amount=(quantity * unit_price).quantize(CENT, rounding=ROUND_HALF_UP),
        hs_code_on_doc=item.hs_code if print_hs_code else None,
    )


def _make_weights(
    rng: random.Random, lines: list[RawLineItem], items: list[CatalogueItem]
) -> tuple[Decimal, Decimal]:
    line_weights = (
        line.quantity * item.kg_per_unit for line, item in zip(lines, items, strict=True)
    )
    net = sum(line_weights, Decimal(0)).quantize(CENT, rounding=ROUND_HALF_UP)
    packaging_percent = rng.randint(MIN_PACKAGING_PERCENT, MAX_PACKAGING_PERCENT)
    gross = (net * (100 + packaging_percent) / 100).quantize(CENT, rounding=ROUND_HALF_UP)
    return net, gross
