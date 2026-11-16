"""CLI: the injected-error set. Each invoice carries exactly one planted error, labelled.

uv run python -m synth.inject --n 60 --seed 2026

Sources are fresh ground-truth invoices with their own seed (never the main dataset's),
so nothing here repeats a dev or test document, and the set has its own folder and
split. Files are JSON only: the guardrails are judged on what an extraction returns, and
a planted Incoterm outside the closed list can't be stored as ground truth. Rendered
documents can follow when an end-to-end run with the LLM needs them.
"""

import argparse
import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel

from app.config import Settings
from app.schemas.raw import RawInvoice
from synth.generate import document_id, refuse_main_dataset_seed
from synth.generator import ANCHOR_DATE, derive_rng, generate_ground_truth, load_catalogue
from synth.manifest import MANIFEST_NAME

ErrorKind = Literal[
    "wrong_line_amount",
    "wrong_total",
    "net_over_gross",
    "invalid_incoterm",
    "future_date",
    "bad_currency",
]
ERROR_KINDS: tuple[ErrorKind, ...] = get_args(ErrorKind)
InjectedSplit = Literal["injected"]
INJECTED_DIR = "synthetic_injected"
INJECTED_PREFIX = "inj"
CENT = Decimal("0.01")
# Terms seen on real invoices that aren't Incoterms 2020 and have no exact equivalent:
# retired rules. C&F and CNF are exact synonyms of CFR, so they aren't errors.
OUTDATED_INCOTERMS = ("DDU", "DAT", "DEQ", "DES", "DAF")
# Ways real invoices write a currency that aren't ISO 4217 codes and aren't exact
# synonyms of one (RMB and US$ are; Rs is ambiguous across rupee currencies).
NON_ISO_CURRENCIES = ("EURO", "RS", "NTD", "YEN", "STG", "USD$")
MAX_FUTURE_DAYS = 180
SWAP_DIGITS = "swap two adjacent digits"
CHANGE_DIGIT = "change one digit"
SHIFT_DECIMAL = "shift the decimal point"
TYPO_METHODS = (SWAP_DIGITS, CHANGE_DIGIT, SHIFT_DECIMAL)


class PlantingError(ValueError):
    """The source invoice has nothing this error can be planted in."""


class PlantedError(BaseModel):
    kind: ErrorKind
    path: str  # the field that now holds the error, e.g. "line_items[2].amount"
    original: str
    planted: str
    method: str


class InjectedDocument(BaseModel):
    id: str
    error: PlantedError
    # What the document says, error included. A plain mapping on purpose: a planted
    # Incoterm outside the closed list can't be a RawInvoice. The eval parses it as the
    # pipeline would, and a schema rejection of the planted field counts as caught.
    invoice: dict[str, Any]
    source: RawInvoice  # the same invoice before the error: the clean control


class InjectedEntry(BaseModel):
    id: str
    split: InjectedSplit = "injected"
    path: str
    kind: ErrorKind  # the label: which error was planted


class InjectedManifest(BaseModel):
    seed: int
    as_of: date  # the day the date check is judged against; future dates are after it
    documents: list[InjectedEntry]


def build_injected(n: int, seed: int, out_dir: Path) -> InjectedManifest:
    """Write `n` invoices with one planted error each, cycling through every error kind."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale_file in out_dir.glob(f"{INJECTED_PREFIX}_*.json"):
        stale_file.unlink()
    catalogue = load_catalogue()
    entries = []
    for index in range(n):
        source = generate_ground_truth(seed, index, catalogue).invoice
        kind = ERROR_KINDS[index % len(ERROR_KINDS)]
        rng = derive_rng(seed, "inject", index)
        invoice, error = plant(kind, source, rng, ANCHOR_DATE)
        doc_id = document_id(index, INJECTED_PREFIX)
        document = InjectedDocument(id=doc_id, error=error, invoice=invoice, source=source)
        path = f"{doc_id}.json"
        (out_dir / path).write_bytes(document.model_dump_json(indent=2).encode())
        entries.append(InjectedEntry(id=doc_id, path=path, kind=kind))
    manifest = InjectedManifest(seed=seed, as_of=ANCHOR_DATE, documents=entries)
    (out_dir / MANIFEST_NAME).write_bytes(manifest.model_dump_json(indent=2).encode())
    return manifest


def load_injected(folder: Path) -> tuple[InjectedManifest, list[InjectedDocument]]:
    manifest = InjectedManifest.model_validate_json((folder / MANIFEST_NAME).read_bytes())
    documents = [
        InjectedDocument.model_validate_json((folder / entry.path).read_bytes())
        for entry in manifest.documents
    ]
    return manifest, documents


def plant(
    kind: ErrorKind, source: RawInvoice, rng: random.Random, as_of: date
) -> tuple[dict[str, Any], PlantedError]:
    """Return the invoice as JSON-ready data with one error planted, and its label."""
    invoice = source.model_dump(mode="json")
    match kind:
        case "wrong_line_amount":
            if not source.line_items:
                raise PlantingError("no line items")
            index = rng.randrange(len(source.line_items))
            original = _required(source.line_items[index].amount, "line amount")
            planted, method = typo(original, rng)
            invoice["line_items"][index]["amount"] = str(planted)
            path = f"line_items[{index}].amount"
        case "wrong_total":
            original = _required(source.total_amount, "total")
            planted, method = typo(original, rng)
            invoice["total_amount"] = str(planted)
            path = "total_amount"
        case "net_over_gross":
            # Columns read or typed the wrong way round, the usual real-world cause.
            net = _required(source.net_weight_kg, "net weight")
            gross = _required(source.gross_weight_kg, "gross weight")
            if net >= gross:
                raise PlantingError("net weight isn't below gross weight")
            invoice["net_weight_kg"], invoice["gross_weight_kg"] = str(gross), str(net)
            original, planted, method = net, gross, "swap net and gross"
            path = "net_weight_kg"
        case "invalid_incoterm":
            original = source.incoterm
            planted = rng.choice(OUTDATED_INCOTERMS)
            invoice["incoterm"] = planted
            path, method = "incoterm", "outdated or informal term"
        case "future_date":
            original = source.invoice_date
            planted = as_of + timedelta(days=rng.randint(1, MAX_FUTURE_DAYS))
            invoice["invoice_date"] = planted.isoformat()
            path, method = "invoice_date", f"up to {MAX_FUTURE_DAYS} days after {as_of}"
        case "bad_currency":
            original = source.currency
            planted = rng.choice(NON_ISO_CURRENCIES)
            invoice["currency"] = planted
            path, method = "currency", "common non-ISO spelling"
    error = PlantedError(
        kind=kind, path=path, original=str(original), planted=str(planted), method=method
    )
    return invoice, error


def typo(value: Decimal, rng: random.Random) -> tuple[Decimal, str]:
    """A realistic keying error in a money amount: never the same value, never <= 0."""
    methods = list(TYPO_METHODS)
    rng.shuffle(methods)
    for method in methods:
        planted = _apply_typo(method, value.quantize(CENT), rng)
        if planted is not None and planted != value and planted > 0:
            return planted, method
    raise PlantingError(f"no typo changes {value}")


def _apply_typo(method: str, value: Decimal, rng: random.Random) -> Decimal | None:
    text = f"{value:.2f}"
    digits = [position for position, character in enumerate(text) if character.isdigit()]
    if method == SHIFT_DECIMAL:
        factor = Decimal(10) if rng.random() < 0.5 else Decimal("0.1")
        return (value * factor).quantize(CENT, rounding=ROUND_HALF_UP)
    if method == SWAP_DIGITS:
        pairs = [(a, b) for a, b in pairwise(digits) if text[a] != text[b]]
        if not pairs:
            return None
        a, b = rng.choice(pairs)
        swapped = list(text)
        swapped[a], swapped[b] = text[b], text[a]
        result = "".join(swapped)
    else:
        position = rng.choice(digits)
        # A leading zero would just shorten the number; choose another digit instead.
        banned = {text[position]} | ({"0"} if position == digits[0] else set())
        replacement = rng.choice([d for d in "0123456789" if d not in banned])
        result = text[:position] + replacement + text[position + 1 :]
    return Decimal(result)


def _required[T](value: T | None, name: str) -> T:
    if value is None:
        raise PlantingError(f"the source has no {name}")
    return value


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the injected-error set.")
    parser.add_argument("--n", type=int, required=True, help="number of invoices")
    parser.add_argument("--seed", type=int, required=True, help="random seed")
    args = parser.parse_args(argv)
    if args.n < len(ERROR_KINDS):
        parser.error(f"--n must be at least {len(ERROR_KINDS)}, one per error kind")
    return args


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    data_dir = Settings().data_dir
    refuse_main_dataset_seed(args.seed, data_dir)
    out_dir = data_dir / INJECTED_DIR
    manifest = build_injected(args.n, args.seed, out_dir)
    print(f"Generated {len(manifest.documents)} injected invoices -> {out_dir}")
    for kind in ERROR_KINDS:
        count = sum(entry.kind == kind for entry in manifest.documents)
        print(f"  {kind:18} {count}")


if __name__ == "__main__":
    main()
