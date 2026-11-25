"""HS codes printed on the invoice: split off a description, and compared with suggestions.

Some forms print the code inside the description cell ("Cotton shirts - 6105.10.4000.8").
It belongs in `hs_code_on_doc`, and it must not reach the HS stage: a model that can read
the exporter's code repeats it, so its agreement would mean nothing.

A code is taken off the end of a description only when it can't be read as anything else:
- it repeats the line's own `hs_code_on_doc` (either may be the longer form); or
- a label names it ("HS Code 61051010", "HTS: 6105.10"; for four digits, only "HS code",
  "HS heading" and the like, because "HS 8413" may be a model); or
- it has three or more dotted groups ("6105.10.4000", "6105.10.4000.8"), unless it reads
  as a date ("2026.11.25"); or
- it has two dotted groups ("6105.10", "6105.1010") set apart by punctuation.
Where the tariff's headings are known, the last two also need a heading that exists.
Sizes, models and plain numbers ("40W", "205/55 R16", "Cable - 1250.50") stay.
"""

import re
from datetime import date

from pydantic import BaseModel

from app.schemas.hs import HSSuggestion

HS_HEADING_DIGITS = 4
HS_INTERNATIONAL_DIGITS = 6
MAX_CODE_DIGITS = 13
LAST_CHAPTER = 97
RESERVED_CHAPTER = 77  # kept empty by the Harmonized System
MIN_DOTTED_GROUPS = 3  # enough to recognise a code without punctuation before it
NOT_A_DIGIT = re.compile(r"\D")
# The end of a description: what sets the code apart, an optional label, the code.
TRAILING_CODE = re.compile(
    r"(?P<gap>\s*[-\u2013\u2014:;,|/(\[]+\s*|\s+)"
    r"(?P<label>(?:h\.?\s?s\.?|hts(?:us)?|hsn|pct|customs\s+tariff|tariff|"
    r"harmoni[sz]ed\s+(?:system|tariff))"
    r"(?:\s*(?:code|no|number|heading))?\s*[.:#-]*\s*)?"
    r"(?P<code>\d{4}(?:[. ]?\d{1,6}){0,4})"
    r"\s*[)\].;,]*\s*$",
    re.IGNORECASE,
)

LABEL_WORD = re.compile(r"code|no\b|number|heading", re.IGNORECASE)


class DescriptionSplit(BaseModel):
    description: str  # without the code; unchanged when there is none
    code: str | None  # as printed


class PrintedCodeMismatch(BaseModel):
    """The code printed on the line and our best suggestion disagree: a person decides."""

    printed: str
    suggested: str
    digits: int  # how many leading digits were compared


def split_printed_code(
    description: str,
    hs_code_on_doc: str | None,
    known_headings: frozenset[str] | None = None,
) -> DescriptionSplit:
    """A description without the HS code printed at its end (see the module docstring)."""
    unchanged = DescriptionSplit(description=description, code=None)
    match = TRAILING_CODE.search(description)
    if match is None:
        return unchanged
    goods, code = description[: match.start()].rstrip(), match["code"].strip()
    if not goods or not _is_hs_shaped(code):
        return unchanged
    recognised = (
        _repeats(code, hs_code_on_doc)
        or _is_labelled(code, match["label"])
        or (_is_unmistakable(code, match["gap"]) and _is_known(code, known_headings))
    )
    return DescriptionSplit(description=goods, code=code) if recognised else unchanged


def same_code(code: str, other: str | None) -> bool:
    """The same code, perhaps printed to a different length."""
    if other is None:
        return False
    left, right = _digits(code), _digits(other)
    if min(len(left), len(right)) < HS_HEADING_DIGITS:
        return False
    return left.startswith(right) or right.startswith(left)


def printed_code_mismatch(
    printed: str | None, suggestions: list[HSSuggestion]
) -> PrintedCodeMismatch | None:
    """Compare a printed code with the top suggestion at 6 digits, or as many as are printed.

    Only the first 6 digits are international; the rest are the exporter's own country's.
    """
    if printed is None or not suggestions:
        return None
    digits = _digits(printed)[:HS_INTERNATIONAL_DIGITS]
    if len(digits) < HS_HEADING_DIGITS:
        return None
    top = suggestions[0].code
    if _digits(top).startswith(digits):
        return None
    return PrintedCodeMismatch(printed=printed, suggested=top, digits=len(digits))


def _repeats(code: str, hs_code_on_doc: str | None) -> bool:
    """The line's own code again. A bare heading must match it exactly: "Bearing 6204" is a
    model, whatever the line's longer code starts with."""
    if not same_code(code, hs_code_on_doc):
        return False
    short = len(_digits(code)) < HS_INTERNATIONAL_DIGITS
    return not short or _digits(code) == _digits(hs_code_on_doc or "")


def _is_labelled(code: str, label: str | None) -> bool:
    if label is None:
        return False
    return len(_digits(code)) >= HS_INTERNATIONAL_DIGITS or bool(LABEL_WORD.search(label))


def _is_known(code: str, known_headings: frozenset[str] | None) -> bool:
    return known_headings is None or _digits(code)[:HS_HEADING_DIGITS] in known_headings


def _is_hs_shaped(code: str) -> bool:
    digits = _digits(code)
    chapter = int(digits[:2])
    return (
        HS_HEADING_DIGITS <= len(digits) <= MAX_CODE_DIGITS
        and 1 <= chapter <= LAST_CHAPTER
        and chapter != RESERVED_CHAPTER
        and int(digits[2:HS_HEADING_DIGITS]) > 0
    )


def _is_unmistakable(code: str, gap: str) -> bool:
    """Without a label and without `hs_code_on_doc` to confirm it, dots must make the case."""
    groups = code.split(".")
    if " " in code or len(groups) < 2 or len(groups[1]) not in (2, 4, 6):
        return False
    if len(groups) >= MIN_DOTTED_GROUPS:
        return not _is_date(groups)
    return bool(gap.strip())


def _is_date(groups: list[str]) -> bool:
    if len(groups) != MIN_DOTTED_GROUPS:
        return False
    try:
        date(int(groups[0]), int(groups[1]), int(groups[2]))
    except ValueError:
        return False
    return groups[0].startswith(("19", "20"))


def _digits(code: str) -> str:
    return NOT_A_DIGIT.sub("", code)
