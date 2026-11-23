"""Parse the FBR Pakistan Customs Tariff PDF into one row per 8-digit PCT code.

uv run python -m tariff.parse --pdf data/tariff/pct_2026-27.pdf --out tariff/pct_2026-27.csv

The schedule's text layer reads line by line as: a heading (`01.02 Live bovine animals.`),
dash groups (`- Cattle:`, `- - Pure-bred breeding animals:`) and code lines
(`0102.2110 - - - Bulls 0`), any of which may wrap onto further lines. A code's duty
ends its text (`20`, or a specific duty such as `Rs. 105 50/ MT`). Chapter 99 (exemption
schedules) follows chapter 97 and isn't part of the code table.

Wrapped text in chapters 40-90 often starts a line with a code it refers to
(`8701.2020, 8701.2040 and`). So a line starts a new code only if the code is followed by
the indent dashes, or is a heading-level `NNNN.0000` followed by its title, or is the next
code of the current heading in ascending order. Codes must ascend, and so must headings.

PDFium isn't thread-safe: run this from one thread only.
"""

import argparse
import csv
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pypdfium2

from app.pipeline.validate import HSCodeTable
from app.schemas.tariff import TariffRow

CODE_START = re.compile(r"^(\d{4}\.\d{4})(.*)$")
BARE_CODE = re.compile(r"^\d{4}\.\d{4}$")
DASHED_REST = re.compile(r"^\s*-")
HEADING_LEVEL_CODE = re.compile(r"^\d{4}\.0000$")
TITLE_START = re.compile(r"^\s+[A-Z]")
# Wrapped text listing codes: "8701.2020, 8701.2040", "8704.3130 and 8704.3190", or a
# code alone at the end of a list.
CODE_LIST_REST = re.compile(r"^\s*([,;&.]|and\b|$)")
HEADING_START = re.compile(r"^(\d{2})\.(\d{2})\s+([A-Z].*)$")
# Chapter 99 entries start with a bare 4-digit number, e.g. "9901" or "9903 Imports by".
CHAPTER_99_START = re.compile(r"^99\d{2}(\s|$)")
# A deleted heading keeps its number in brackets, e.g. "[05.03]".
DELETED_HEADING = re.compile(r"^\[\d{2}\.\d{2}\]$")
# Sub-chapter titles in chapters 28, 29 and 62-63, in capitals: "III.- HALOGEN OR",
# "I.CHEMICAL ELEMENTS", "XI- PRO- VITAMINS", "II. SETS". Their wrapped lines follow.
SECTION_TITLE = re.compile(r"^[IVX]+\s*[.-]+\s*[A-Z][^a-z]*$")
LEADING_DASHES = re.compile(r"^((?:-\s*)+)")
TRAILING_DUTY = re.compile(r"\s+(?:\d+(?:\.\d+)?|Rs\.?\s*[\d ]+\s*(?:/\s*[A-Za-z ]+|per [a-z]+))$")
# Spreadsheet errors printed in the schedule instead of a description (8414.1000).
SPREADSHEET_ERROR = re.compile(r"^#[A-Z/0]+[?!]?$")
# Repeated on every page; never part of an entry.
PAGE_FURNITURE = frozenset(
    {"PAKISTAN CUSTOMS TARIFF FY 2026-27", "2026-27", "CD (%)", "PCT CODE DESCRIPTION"}
)
# PDFium writes \x02 (our PDFs) or U+FFFE (this one) for a hyphen wrapped after a letter.
WRAPPED_HYPHENS = ("\x02", "\ufffe")
# U+FFFD replaces glyphs the text layer can't decode (superscripts, accents, quotes).
UNDECODED_GLYPH = "\ufffd"
PATH_SEPARATOR = " > "
CSV_FIELDS = ("code", "description", "path")
# The parsed schedule, committed: evals and the HS-code check read it without a database.
TARIFF_CSV = Path(__file__).resolve().parent / "pct_2026-27.csv"


class TariffParseError(ValueError):
    """The schedule's text didn't have the expected structure; never guess a row."""


@dataclass
class ParsedTariff:
    rows: list[TariffRow] = field(default_factory=list)
    # Headings whose title line is missing from the text layer: their codes follow the
    # previous heading's directly (11.03 in the 2026-27 PDF). Paths start at the first group.
    untitled_headings: list[str] = field(default_factory=list)
    # Codes whose printed description is unreadable (a spreadsheet error such as #NAME?):
    # their own description is empty and their path ends at the parent group.
    unreadable_descriptions: list[str] = field(default_factory=list)
    # Codes with no duty at the end of their text (some long chapter 87 entries print it
    # after the first sub-item); their descriptions keep the rest as printed.
    without_trailing_duty: list[str] = field(default_factory=list)


@dataclass
class _Block:
    """One structural line plus the wrapped lines that continue it."""

    kind: str  # "code" | "heading" | "group" | "ignored"
    key: str  # the code, or the heading's 4 digits; "" otherwise
    lines: list[str]

    @property
    def text(self) -> str:
        return _clean(" ".join(self.lines))


@dataclass
class _Scope:
    """Where the reader is in the schedule; decides whether a line starts a new entry."""

    heading: str = ""  # 4 digits, e.g. "0102"
    last_code: str = ""

    def starts_code(self, code: str, rest: str) -> bool:
        follows = code > self.last_code
        is_heading_code = HEADING_LEVEL_CODE.match(code) and TITLE_START.match(rest)
        if DASHED_REST.match(rest) or is_heading_code:
            if not follows:
                raise TariffParseError(f"{code}: printed after {self.last_code}")
            return True
        return self.is_next_code(code) and not CODE_LIST_REST.match(rest)

    def is_next_code(self, code: str) -> bool:
        return code > self.last_code and code[:4] == self.heading

    def starts_heading(self, digits: str) -> bool:
        return digits > self.heading


def parse_lines(lines: Iterable[str]) -> ParsedTariff:
    """Turn the schedule's text lines into tariff rows, in printed order."""
    result = ParsedTariff()
    titled_heading = ""
    heading_text = ""
    groups: dict[int, str] = {}
    previous_depth = 1
    for block in _blocks(lines):
        if block.kind == "heading":
            titled_heading, heading_text = block.key, _strip_end(block.text)
            groups.clear()
        elif block.kind == "group":
            depth, text = _split_dashes(block.text)
            _drop_from(groups, depth)
            groups[depth] = _strip_end(text)
        elif block.kind == "code":
            depth, own_text = _code_parts(block, result)
            if depth == 0 and not HEADING_LEVEL_CODE.match(block.key):
                # Printed without its indent dashes (8708.1020): a sibling of the code above.
                depth = previous_depth
            previous_depth = max(depth, 1)
            if depth == 0:  # the code is its own heading, e.g. "0409.0000 Natural honey. 20"
                titled_heading, heading_text = block.key[:4], own_text
                groups.clear()
            elif block.key[:4] != titled_heading:
                titled_heading, heading_text = block.key[:4], ""
                result.untitled_headings.append(titled_heading)
            parents = [groups[level] for level in sorted(groups) if level < depth]
            parts = [own_text] if depth == 0 else [heading_text, *parents, own_text]
            path = PATH_SEPARATOR.join(part for part in parts if part)
            result.rows.append(TariffRow(code=block.key, description=own_text, path=path))
            _drop_from(groups, depth)
    if not result.rows:
        raise TariffParseError("no tariff rows found")
    return result


def _blocks(lines: Iterable[str]) -> Iterator[_Block]:
    scope = _Scope()
    current: _Block | None = None
    # A code alone on its line: its text starts on the next line if that one is dashed
    # (8479.8960); otherwise it ends a list of codes in wrapped text.
    bare_code: str | None = None
    for raw in lines:
        line = raw.strip()
        if not line or line in PAGE_FURNITURE:
            continue
        if CHAPTER_99_START.match(line):
            break
        if bare_code is not None:
            if line.startswith("-"):
                line = f"{bare_code} {line}"
            elif current is not None:
                current.lines.append(bare_code)
            bare_code = None
        elif BARE_CODE.match(line) and scope.is_next_code(line):
            bare_code = line
            continue
        new = _new_block(line, scope)
        if new is None:
            if current is None:
                raise TariffParseError(f"text before the first heading: {line[:80]!r}")
            current.lines.append(line)
            continue
        if current is not None:
            yield current
        current = new
    if current is not None:
        yield current


def _new_block(line: str, scope: _Scope) -> _Block | None:
    """The block this line starts, or None when it continues the current one."""
    code = CODE_START.match(line)
    if code and scope.starts_code(code.group(1), code.group(2)):
        scope.last_code = code.group(1)
        scope.heading = code.group(1)[:4]
        return _Block("code", scope.last_code, [code.group(2)])
    heading = HEADING_START.match(line)
    if heading and scope.starts_heading(heading.group(1) + heading.group(2)):
        scope.heading = heading.group(1) + heading.group(2)
        return _Block("heading", scope.heading, [heading.group(3)])
    if line.startswith("-"):
        return _Block("group", "", [line])
    if DELETED_HEADING.match(line) or SECTION_TITLE.match(line):
        return _Block("ignored", "", [line])
    return None


def _code_parts(block: _Block, result: ParsedTariff) -> tuple[int, str]:
    """A code line's indent depth and its own description, without the duty."""
    text = block.text
    duty = TRAILING_DUTY.search(text)
    if duty is None:
        result.without_trailing_duty.append(block.key)
    else:
        text = text[: duty.start()]
    depth, own_text = _split_dashes(text)
    own_text = _strip_end(own_text)
    if SPREADSHEET_ERROR.match(own_text):
        result.unreadable_descriptions.append(block.key)
        own_text = ""
    elif not own_text:
        raise TariffParseError(f"{block.key}: no description")
    return depth, own_text


def _split_dashes(text: str) -> tuple[int, str]:
    """`- - Other` -> (2, "Other"): the dash count is the indent level in the schedule."""
    match = LEADING_DASHES.match(text)
    if match is None:
        return 0, text
    return match.group(1).count("-"), text[match.end() :]


def _drop_from(groups: dict[int, str], depth: int) -> None:
    """A line at `depth` ends every open group at that depth or deeper."""
    for level in [level for level in groups if level >= depth]:
        del groups[level]


def _strip_end(text: str) -> str:
    return text.strip().rstrip(":;.").strip()


def _clean(text: str) -> str:
    for hyphen in WRAPPED_HYPHENS:
        text = text.replace(hyphen, "-")
    return " ".join(text.replace(UNDECODED_GLYPH, "").split())


def read_pdf_lines(pdf_path: Path) -> Iterator[str]:
    """The text layer of every page, line by line."""
    pdf = pypdfium2.PdfDocument(pdf_path)
    try:
        for page in pdf:
            yield from page.get_textpage().get_text_range().splitlines()
    finally:
        pdf.close()


def write_csv(rows: list[TariffRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.model_dump(mode="json"))


def read_csv(path: Path) -> list[TariffRow]:
    with path.open(newline="", encoding="utf-8") as file:
        return [TariffRow.model_validate(record) for record in csv.DictReader(file)]


def load_hs_code_table(path: Path = TARIFF_CSV) -> HSCodeTable:
    return HSCodeTable.from_codes(row.code for row in read_csv(path))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    parsed = parse_lines(read_pdf_lines(args.pdf))
    write_csv(parsed.rows, args.out)
    chapters = sorted({row.chapter for row in parsed.rows})
    print(f"Parsed {len(parsed.rows)} codes in {len(chapters)} chapters -> {args.out}")
    print(f"Headings with no title line: {parsed.untitled_headings or 'none'}")
    print(f"Unreadable descriptions: {parsed.unreadable_descriptions or 'none'}")
    print(f"Codes with no trailing duty: {len(parsed.without_trailing_duty)}")


if __name__ == "__main__":
    main()
