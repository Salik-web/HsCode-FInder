"""Scoring: compare one extracted invoice with its ground truth, then aggregate.

A bug here would invalidate every number the project reports, so each rule is explicit
and unit-tested in tests/test_metrics.py.

Missing values follow one rule everywhere: both None is correct, a value where the truth
is None is a hallucination, and None where the truth has a value is a miss. A document
with no validated output scores every field as `no_output`, never as a lucky None match.
"""

import re
import unicodedata
from collections.abc import Iterable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, get_args

from pydantic import BaseModel, computed_field
from rapidfuzz import fuzz
from scipy.optimize import linear_sum_assignment

from app.schemas.raw import RawInvoice, RawLineItem, RawParty

Outcome = Literal["correct", "wrong", "missed", "hallucinated", "no_output"]
OUTCOMES: tuple[Outcome, ...] = get_args(Outcome)
Comparison = Literal["exact", "code", "number", "date", "fuzzy"]
Scalar = str | Decimal | date

FUZZY_THRESHOLD = 0.9
# Lines whose descriptions are less similar than this are never paired: they count as one
# dropped and one extra line rather than a pair with every field wrong.
LINE_MATCH_THRESHOLD = 0.5
# Too small to outweigh any real description difference; only breaks ties between lines
# with identical descriptions, in favour of the pairing where more other fields agree.
TIE_BREAK_WEIGHT = 0.001
UNMATCHABLE_COST = 10.0
CENT = Decimal("0.01")
NON_WORD = re.compile(r"[\W_]+")

HEADER_FIELDS: dict[str, Comparison] = {
    "invoice_number": "exact",
    "invoice_date": "date",
    "incoterm": "code",
    "currency": "code",
    "total_amount": "number",
    "gross_weight_kg": "number",
    "net_weight_kg": "number",
}
PARTIES = ("shipper", "buyer", "consignee", "notify_party")
PARTY_FIELDS: dict[str, Comparison] = {"name": "fuzzy", "address": "fuzzy", "country": "code"}
LINE_FIELDS: dict[str, Comparison] = {
    "description": "fuzzy",
    "quantity": "number",
    "unit": "code",
    "unit_price": "number",
    "amount": "number",
    "hs_code_on_doc": "exact",
}
LINE_PREFIX = "line"
CRITICAL_HEADER_FIELDS = ("invoice_number", "total_amount", "currency", "incoterm")
CRITICAL_LINE_FIELD = "amount"
CRITICAL_LINE_NAME = f"{LINE_PREFIX}.{CRITICAL_LINE_FIELD}"
EMPTY_PARTY = RawParty(name=None, address=None, country=None)


class FieldScore(BaseModel):
    field: str  # e.g. "invoice_number", "buyer.name", "line.amount"
    outcome: Outcome
    expected: str | None
    predicted: str | None


class LineCounts(BaseModel):
    gold: int
    predicted: int
    matched: int


class DocumentScore(BaseModel):
    """Header, party and matched-line fields, plus the critical fields that gate a document."""

    fields: list[FieldScore]
    lines: LineCounts
    # Critical header fields, then one line.amount entry per gold line (missed when the
    # line was dropped) and per extra predicted line (hallucinated).
    critical: list[FieldScore]

    @computed_field
    @property
    def all_critical_correct(self) -> bool:
        return all(score.outcome == "correct" for score in self.critical)


class FieldAccuracy(BaseModel):
    field: str
    total: int
    correct: int
    wrong: int
    missed: int
    hallucinated: int
    no_output: int

    @computed_field
    @property
    def accuracy(self) -> float | None:
        return ratio(self.correct, self.total)


class Summary(BaseModel):
    documents: int
    fields: list[FieldAccuracy]
    line_precision: float | None
    line_recall: float | None
    line_f1: float | None
    critical_field_accuracy: float | None
    all_critical_correct_rate: float | None


def ratio(numerator: int, denominator: int) -> float | None:
    """None, not 0, when there is nothing to divide by: no data is not a zero score."""
    return None if denominator == 0 else numerator / denominator


def normalise_text(text: str) -> str:
    """Case, Unicode form, punctuation and spacing don't change what a name or address says."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return " ".join(NON_WORD.sub(" ", folded).split())


def similarity(expected: str, predicted: str) -> float:
    return fuzz.ratio(normalise_text(expected), normalise_text(predicted)) / 100


def _to_cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def values_equal(kind: Comparison, expected: Scalar, predicted: Scalar) -> bool:
    match kind:
        case "exact":
            return str(expected).strip() == str(predicted).strip()
        case "code":
            return str(expected).strip().casefold() == str(predicted).strip().casefold()
        case "number":
            return _to_cents(Decimal(expected)) == _to_cents(Decimal(predicted))
        case "date":
            return expected == predicted
        case "fuzzy":
            return similarity(str(expected), str(predicted)) >= FUZZY_THRESHOLD


def compare(kind: Comparison, expected: Scalar | None, predicted: Scalar | None) -> Outcome:
    if predicted is None:
        return "correct" if expected is None else "missed"
    if expected is None:
        return "hallucinated"
    return "correct" if values_equal(kind, expected, predicted) else "wrong"


def _as_text(value: Scalar | None) -> str | None:
    return None if value is None else str(value)


def _score_field(
    name: str, kind: Comparison, expected: Scalar | None, predicted: Scalar | None
) -> FieldScore:
    return FieldScore(
        field=name,
        outcome=compare(kind, expected, predicted),
        expected=_as_text(expected),
        predicted=_as_text(predicted),
    )


def _no_output_field(name: str, expected: Scalar | None) -> FieldScore:
    return FieldScore(field=name, outcome="no_output", expected=_as_text(expected), predicted=None)


def _header_and_party_values(invoice: RawInvoice) -> dict[str, Scalar | None]:
    """Flatten every non-line field to its scored name; a missing party has all-None fields."""
    values: dict[str, Scalar | None] = {name: getattr(invoice, name) for name in HEADER_FIELDS}
    for party_name in PARTIES:
        party = getattr(invoice, party_name) or EMPTY_PARTY
        for field in PARTY_FIELDS:
            values[f"{party_name}.{field}"] = getattr(party, field)
    return values


def _comparison_for(name: str) -> Comparison:
    if name in HEADER_FIELDS:
        return HEADER_FIELDS[name]
    return PARTY_FIELDS[name.split(".", 1)[1]]


def _description_similarity(gold: RawLineItem, predicted: RawLineItem) -> float:
    if gold.description is None or predicted.description is None:
        return 0.0
    return similarity(gold.description, predicted.description)


def _other_field_mismatches(gold: RawLineItem, predicted: RawLineItem) -> int:
    return sum(
        compare(kind, getattr(gold, field), getattr(predicted, field)) != "correct"
        for field, kind in LINE_FIELDS.items()
        if field != "description"
    )


def _pair_cost(gold: RawLineItem, predicted: RawLineItem) -> float:
    description_similarity = _description_similarity(gold, predicted)
    if description_similarity < LINE_MATCH_THRESHOLD:
        return UNMATCHABLE_COST
    tie_break = TIE_BREAK_WEIGHT * _other_field_mismatches(gold, predicted)
    return 1 - description_similarity + tie_break


def match_lines(gold: list[RawLineItem], predicted: list[RawLineItem]) -> list[tuple[int, int]]:
    """Pair gold and predicted lines one-to-one by description (Hungarian matching).

    Order on the page doesn't matter; pairs below LINE_MATCH_THRESHOLD are dropped.
    """
    if not gold or not predicted:
        return []
    costs = [[_pair_cost(g, p) for p in predicted] for g in gold]
    gold_indices, predicted_indices = linear_sum_assignment(costs)
    return [
        (int(g), int(p))
        for g, p in zip(gold_indices, predicted_indices, strict=True)
        if costs[g][p] < UNMATCHABLE_COST
    ]


def _score_line_pair(gold: RawLineItem, predicted: RawLineItem) -> list[FieldScore]:
    return [
        _score_field(
            f"{LINE_PREFIX}.{field}", kind, getattr(gold, field), getattr(predicted, field)
        )
        for field, kind in LINE_FIELDS.items()
    ]


def _critical_line_scores(
    gold: list[RawLineItem], predicted: list[RawLineItem], pairs: list[tuple[int, int]]
) -> list[FieldScore]:
    matched_gold = {g: p for g, p in pairs}
    matched_predicted = set(matched_gold.values())
    scores = []
    for index, line in enumerate(gold):
        predicted_amount = None
        outcome: Outcome = "missed"
        if index in matched_gold:
            predicted_amount = predicted[matched_gold[index]].amount
            outcome = compare("number", line.amount, predicted_amount)
        scores.append(
            FieldScore(
                field=CRITICAL_LINE_NAME,
                outcome=outcome,
                expected=_as_text(line.amount),
                predicted=_as_text(predicted_amount),
            )
        )
    for index, line in enumerate(predicted):
        if index not in matched_predicted:
            scores.append(
                FieldScore(
                    field=CRITICAL_LINE_NAME,
                    outcome="hallucinated",
                    expected=None,
                    predicted=_as_text(line.amount),
                )
            )
    return scores


def _score_no_output(truth: RawInvoice, expected_values: dict[str, Scalar | None]) -> DocumentScore:
    fields = [_no_output_field(name, value) for name, value in expected_values.items()]
    critical = [_no_output_field(name, expected_values[name]) for name in CRITICAL_HEADER_FIELDS]
    critical += [_no_output_field(CRITICAL_LINE_NAME, line.amount) for line in truth.line_items]
    lines = LineCounts(gold=len(truth.line_items), predicted=0, matched=0)
    return DocumentScore(fields=fields, lines=lines, critical=critical)


def score_document(truth: RawInvoice, predicted: RawInvoice | None) -> DocumentScore:
    """Score one document. `predicted` is None when extraction produced no validated output."""
    expected_values = _header_and_party_values(truth)
    if predicted is None:
        return _score_no_output(truth, expected_values)

    predicted_values = _header_and_party_values(predicted)
    fields = [
        _score_field(name, _comparison_for(name), expected, predicted_values[name])
        for name, expected in expected_values.items()
    ]
    pairs = match_lines(truth.line_items, predicted.line_items)
    for g, p in pairs:
        fields += _score_line_pair(truth.line_items[g], predicted.line_items[p])
    by_name = {score.field: score for score in fields}
    critical = [by_name[name] for name in CRITICAL_HEADER_FIELDS]
    critical += _critical_line_scores(truth.line_items, predicted.line_items, pairs)
    lines = LineCounts(
        gold=len(truth.line_items), predicted=len(predicted.line_items), matched=len(pairs)
    )
    return DocumentScore(fields=fields, lines=lines, critical=critical)


def _field_order(name: str) -> tuple[int, str]:
    """Header fields, then parties, then line fields, each in schema order."""
    known = [*HEADER_FIELDS]
    known += [f"{party}.{field}" for party in PARTIES for field in PARTY_FIELDS]
    known += [f"{LINE_PREFIX}.{field}" for field in LINE_FIELDS]
    return (known.index(name), name) if name in known else (len(known), name)


def accuracy_by_field(scores: Iterable[FieldScore]) -> list[FieldAccuracy]:
    counts: dict[str, dict[Outcome, int]] = {}
    for score in scores:
        per_outcome = counts.setdefault(score.field, dict.fromkeys(OUTCOMES, 0))
        per_outcome[score.outcome] += 1
    return [
        FieldAccuracy(field=name, total=sum(per_outcome.values()), **per_outcome)
        for name, per_outcome in sorted(counts.items(), key=lambda item: _field_order(item[0]))
    ]


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def summarise(scores: list[DocumentScore]) -> Summary:
    """Micro-averaged totals: every field and every line counts once, whatever its document."""
    matched = sum(score.lines.matched for score in scores)
    precision = ratio(matched, sum(score.lines.predicted for score in scores))
    recall = ratio(matched, sum(score.lines.gold for score in scores))
    critical = [field for score in scores for field in score.critical]
    return Summary(
        documents=len(scores),
        fields=accuracy_by_field(field for score in scores for field in score.fields),
        line_precision=precision,
        line_recall=recall,
        line_f1=_f1(precision, recall),
        critical_field_accuracy=ratio(
            sum(field.outcome == "correct" for field in critical), len(critical)
        ),
        all_critical_correct_rate=ratio(
            sum(score.all_critical_correct for score in scores), len(scores)
        ),
    )
