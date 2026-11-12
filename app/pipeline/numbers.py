"""Normalise number strings in a model answer before validation.

The prompt asks for plain digits with a dot for decimals, but models copy what's printed:
"207,732.88", "USD 1 234.50", "1.234,50". This turns such strings into plain decimals
when the format is certain, and reports them as ambiguous when it isn't. Never guess.

A string's format is certain when:
- it has no separator;
- it has both "," and ".": the last one is the decimal mark;
- one separator appears more than once: it groups thousands;
- one separator appears once and can't be grouping thousands: not exactly 3 digits after
  it, more than 3 before it, or only zeros before it.

That leaves one separator, once, with 1-3 digits before and exactly 3 after ("1,234",
"1.234"). The document's other numbers decide: if every certain number with a decimal
mark uses the same one, that wins. If they conflict or there are none, the value stays
ambiguous and the document goes to review.

Currency alone doesn't decide: an invoice in EUR from a UK seller still writes 1,234.50.
"""

import re
from decimal import Decimal
from typing import Any, Literal, get_args

from pydantic import BaseModel

from app.schemas.compact import LINE_COLUMNS, CompactInvoice, CompactLine
from app.schemas.raw import RawInvoice, RawLineItem

DecimalMark = Literal[".", ","]

# Currency symbols, letters (USD, Rs), spaces of every width and apostrophes used as
# thousands separators ("1'234.50") carry no numeric value.
NON_NUMERIC = re.compile(r"[^\d.,\-]")
# A word with its abbreviation dot ("Rs.", "Nos."), removed first so the dot isn't read
# as a decimal mark.
WORD_WITH_DOT = re.compile(r"[^\W\d_]+\.?")
NUMBER_SHAPE = re.compile(r"^-?\d[\d.,]*$")
THOUSANDS_GROUP = 3
PLACEHOLDER = "0"


def _decimal_fields(model: type[BaseModel]) -> tuple[str, ...]:
    return tuple(
        name for name, field in model.model_fields.items() if Decimal in get_args(field.annotation)
    )


HEADER_NUMBER_FIELDS = _decimal_fields(RawInvoice)
LINE_NUMBER_FIELDS = _decimal_fields(RawLineItem)
COMPACT_NUMBER_POSITIONS = tuple(
    position
    for position, column_type in enumerate(get_args(CompactLine))
    if Decimal in get_args(column_type)
)


class NumberReport(BaseModel):
    normalised: list[str]  # paths whose string changed, e.g. "line_items[0].amount"
    ambiguous: list[str]  # paths left unchanged because their format isn't certain


class NumberReading(BaseModel):
    """How one string reads: a certain value, two candidates, or not a number at all."""

    certain: str | None = None
    proves: DecimalMark | None = None  # the decimal mark this string shows the document uses
    if_dot: str | None = None  # the value if "." is the decimal mark (ambiguous only)
    if_comma: str | None = None  # the value if "," is the decimal mark (ambiguous only)

    @property
    def ambiguous(self) -> bool:
        return self.if_dot is not None


def normalise_numbers(data: Any, model: type[BaseModel]) -> tuple[Any, NumberReport]:
    """Rewrite number strings in a parsed answer for `model`.

    Values that aren't strings, or don't look like numbers, are left for validation.
    """
    if not isinstance(data, dict):
        return data, NumberReport(normalised=[], ambiguous=[])
    slots = _number_slots(data, model)
    readings = {path: read_number(text) for path, text in slots.items()}
    convention = _document_convention(list(readings.values()))
    result = _copy(data)
    normalised: list[str] = []
    ambiguous: list[str] = []
    for path, reading in readings.items():
        value = reading.certain
        if reading.ambiguous and convention is not None:
            value = reading.if_dot if convention == "." else reading.if_comma
        if reading.ambiguous and value is None:
            ambiguous.append(path)
        elif value is not None and value != slots[path]:
            _set(result, path, value)
            normalised.append(path)
    return result, NumberReport(normalised=normalised, ambiguous=ambiguous)


def with_placeholders(data: dict[str, Any], paths: list[str]) -> dict[str, Any]:
    """A copy with each ambiguous number replaced by "0", so the rest of the answer can be
    validated on its own. Only for checking: the placeholder never reaches a result."""
    result = _copy(data)
    for path in paths:
        _set(result, path, PLACEHOLDER)
    return result


def read_number(text: str) -> NumberReading:
    cleaned = NON_NUMERIC.sub("", WORD_WITH_DOT.sub("", text))
    if not NUMBER_SHAPE.match(cleaned):
        return NumberReading()  # not a number: validation decides
    sign = "-" if cleaned.startswith("-") else ""
    body = cleaned.removeprefix("-")
    dots, commas = body.count("."), body.count(",")
    if not dots and not commas:
        return NumberReading(certain=sign + body)
    if dots and commas:
        mark: DecimalMark = "." if body.rfind(".") > body.rfind(",") else ","
        whole, fraction = body.rsplit(mark, 1)
        if mark in whole:
            return NumberReading()  # "1.234.567,8.9": malformed
        return _grouped(sign, whole, fraction, thousands=_other(mark), proves=mark)
    separator: DecimalMark = "." if dots else ","
    if body.count(separator) > 1:
        return _grouped(sign, body, "", thousands=separator, proves=_other(separator))
    whole, fraction = body.split(separator)
    if len(fraction) != THOUSANDS_GROUP or len(whole) > THOUSANDS_GROUP or not whole.strip("0"):
        return _grouped(sign, whole, fraction, thousands=None, proves=separator)
    grouped, decimal = f"{sign}{whole}{fraction}", f"{sign}{whole}.{fraction}"
    if separator == ",":
        return NumberReading(if_dot=grouped, if_comma=decimal)
    return NumberReading(if_dot=decimal, if_comma=grouped)


def _other(mark: DecimalMark) -> DecimalMark:
    return "," if mark == "." else "."


def _grouped(
    sign: str, whole: str, fraction: str, thousands: str | None, proves: DecimalMark
) -> NumberReading:
    """A certain value, if `whole` is grouped in threes by `thousands` (or not grouped)."""
    groups = whole.split(thousands) if thousands else [whole]
    if len(groups) > 1 and (
        not 1 <= len(groups[0]) <= THOUSANDS_GROUP
        or any(len(group) != THOUSANDS_GROUP for group in groups[1:])
    ):
        return NumberReading()  # "12,34.5": malformed grouping, let validation reject it
    digits = "".join(groups)
    value = f"{sign}{digits}.{fraction}" if fraction else f"{sign}{digits}"
    return NumberReading(certain=value, proves=proves)


def _document_convention(readings: list[NumberReading]) -> DecimalMark | None:
    marks = {reading.proves for reading in readings if reading.proves is not None}
    return marks.pop() if len(marks) == 1 else None


def _number_slots(data: dict[str, Any], model: type[BaseModel]) -> dict[str, str]:
    """Path -> string for every place the schema expects a number."""
    slots = {name: data[name] for name in HEADER_NUMBER_FIELDS if isinstance(data.get(name), str)}
    lines = data.get("line_items")
    if not isinstance(lines, list):
        return slots
    for index, line in enumerate(lines):
        if issubclass(model, CompactInvoice) and isinstance(line, list):
            for position in COMPACT_NUMBER_POSITIONS:
                if position < len(line) and isinstance(line[position], str):
                    slots[f"line_items[{index}].{LINE_COLUMNS[position]}"] = line[position]
        elif isinstance(line, dict):
            for name in LINE_NUMBER_FIELDS:
                if isinstance(line.get(name), str):
                    slots[f"line_items[{index}].{name}"] = line[name]
    return slots


def _copy(data: dict[str, Any]) -> dict[str, Any]:
    """Copy deep enough that setting a line value never changes the caller's data."""
    result = dict(data)
    if isinstance(data.get("line_items"), list):
        result["line_items"] = [
            list(line) if isinstance(line, list) else dict(line) if isinstance(line, dict) else line
            for line in data["line_items"]
        ]
    return result


def _set(data: dict[str, Any], path: str, value: str) -> None:
    if not path.startswith("line_items["):
        data[path] = value
        return
    index_text, name = path.removeprefix("line_items[").split("].")
    line = data["line_items"][int(index_text)]
    if isinstance(line, list):
        line[LINE_COLUMNS.index(name)] = value
    else:
        line[name] = value
