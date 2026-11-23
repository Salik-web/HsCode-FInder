"""Human review: document status, corrections, export. Pure functions, no I/O.

A reviewer's edit never overwrites what the model returned: it is stored as a correction
and replayed onto the run's invoice, and the result is validated with the same schema
as the model's output (design rule 1 holds for human input too).
"""

import csv
import io
import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from app.pipeline.hs_suggest import HSLineResult
from app.schemas.raw import RawInvoice, RawLineItem, RawParty
from app.schemas.result import PipelineResult

# hs_queued: a reviewer asked the worker to re-run HS for the lines whose HS step failed.
DocumentStatus = Literal[
    "queued", "processing", "hs_queued", "needs_review", "ready", "approved", "failed"
]
# A person can correct, choose HS codes and approve only in these.
REVIEWABLE_STATUSES: tuple[DocumentStatus, ...] = ("needs_review", "ready")
# A path names one leaf field: "invoice_number", "buyer.name", "line_items[2].amount".
PATH_PART = re.compile(r"^(?P<name>[a-z_]+)(?:\[(?P<index>\d+)\])?$")
SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
MAX_FILENAME_CHARS = 120
OPTIONAL_PARTIES = ("notify_party",)
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")
CSV_COLUMNS = (
    "invoice_number",
    "invoice_date",
    "currency",
    "incoterm",
    "line",
    "description",
    "quantity",
    "unit",
    "unit_price",
    "amount",
    "hs_code_on_doc",
    "hs_code_chosen",
)


class InvalidCorrectionError(ValueError):
    """The path names no field, or the value doesn't fit the field's type."""


class CorrectionInput(BaseModel):
    field_path: str
    new_value: Any


def status_after_run(result: PipelineResult, hs: list[HSLineResult]) -> DocumentStatus:
    """failed: no invoice came out. needs_review: any field, or any line's HS step, needs a
    person. ready: every field passed. HS codes are suggestions either way."""
    if result.invoice is None:
        return "needs_review" if result.extract.status == "needs_review" else "failed"
    fields_ok = all(assessment.status == "ok" for assessment in result.assessments)
    hs_ok = all(line.hs.stage.status == "ok" for line in hs)
    return "ready" if fields_ok and hs_ok else "needs_review"


def lines_needing_hs(hs: dict[int, HSLineResult]) -> list[int]:
    """Lines whose HS step didn't finish (rate limit, failed validation): worth re-running."""
    return sorted(index for index, line in hs.items() if line.hs.stage.status != "ok")


def field_value(invoice: RawInvoice, path: str) -> Any:
    container, name = _locate(invoice.model_dump(mode="json"), path)
    return container[name]


def apply_corrections(invoice: RawInvoice, corrections: list[CorrectionInput]) -> RawInvoice:
    """Replay corrections in order; the result must validate as a RawInvoice."""
    data = invoice.model_dump(mode="json")
    for correction in corrections:
        container, name = _locate(data, correction.field_path)
        container[name] = correction.new_value
    try:
        return RawInvoice.model_validate(data)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in detail['loc'])}: {detail['msg']}"
            for detail in error.errors(include_url=False, include_input=False)
        )
        raise InvalidCorrectionError(details) from error


def _locate(data: dict[str, Any], path: str) -> tuple[dict[str, Any], str]:
    """The dict holding the leaf a path names, and the leaf's key."""
    parts = path.split(".")
    node: Any = data
    for depth, part in enumerate(parts):
        match = PATH_PART.match(part)
        if match is None or not isinstance(node, dict) or match["name"] not in node:
            raise InvalidCorrectionError(f"{path}: no such field")
        name, index = match["name"], match["index"]
        is_leaf = depth == len(parts) - 1
        if index is None and is_leaf:
            if isinstance(node[name], dict | list):
                raise InvalidCorrectionError(f"{path}: not a single field")
            return node, name
        if node[name] is None and name in OPTIONAL_PARTIES:
            # A party the model didn't find: reading gives None, correcting adds the party.
            node[name] = dict.fromkeys(RawParty.model_fields)
        node = node[name]
        if index is not None:
            if not isinstance(node, list) or int(index) >= len(node) or is_leaf:
                raise InvalidCorrectionError(f"{path}: no such field")
            node = node[int(index)]
    raise InvalidCorrectionError(f"{path}: no such field")


def safe_filename(name: str) -> str:
    """For display only; files are stored under their sha256, never under this name."""
    cleaned = SAFE_FILENAME.sub("_", name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]).strip("._")
    return cleaned[:MAX_FILENAME_CHARS] or "document"


def export_json(
    invoice: RawInvoice, chosen: dict[int, str | None], provenance: dict[str, Any]
) -> dict[str, Any]:
    data = invoice.model_dump(mode="json")
    for index, line in enumerate(data["line_items"]):
        line["hs_code_chosen"] = chosen.get(index)
    return {"invoice": data, "provenance": provenance}


def export_csv(invoice: RawInvoice, chosen: dict[int, str | None]) -> str:
    """One row per line item, with the header fields repeated, for spreadsheets."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for index, line in enumerate(invoice.line_items):
        writer.writerow(_csv_row(invoice, index, line, chosen.get(index)))
    return buffer.getvalue()


def _csv_row(
    invoice: RawInvoice, index: int, line: RawLineItem, chosen: str | None
) -> dict[str, str]:
    values: dict[str, object] = {
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date,
        "currency": invoice.currency,
        "incoterm": invoice.incoterm,
        "line": index + 1,
        "description": line.description,
        "quantity": line.quantity,
        "unit": line.unit,
        "unit_price": line.unit_price,
        "amount": line.amount,
        "hs_code_on_doc": line.hs_code_on_doc,
        "hs_code_chosen": chosen,
    }
    return {key: _cell(value) for key, value in values.items()}


def _cell(value: object) -> str:
    """Empty for "not on the document"; Decimals as written, never in float form.

    Text comes from an untrusted document: a leading =, +, -, @ or control character
    would make a spreadsheet run it as a formula, so such text gets a leading quote.
    """
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")
    text = str(value)
    return f"'{text}" if text.startswith(FORMULA_STARTS) else text
