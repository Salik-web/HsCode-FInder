"""CLI: guardrail eval. Checks should fire on wrong values, and only on wrong values.

uv run python -m evals.guardrails

No LLM is called. Two measurements here:
- Catch rate on the injected set (synth/inject.py): is the planted field flagged, either
  by a check or by the extraction schema rejecting it, as the pipeline would?
- False alarms on clean ground truth: a correct invoice should pass every check.
The third, alarms on cached extractions, is in every evals.run report.

Layouts don't enter here: no page is read, so there is no seen/unseen split to report.
"""

from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ValidationError, computed_field

from app.config import Settings
from app.pipeline.rules import map_code_synonyms
from app.pipeline.validate import verify_invoice
from app.schemas.raw import RawInvoice
from evals.dataset import load_documents
from evals.metrics import ratio
from evals.run import git_state
from synth.inject import (
    ERROR_KINDS,
    INJECTED_DIR,
    ErrorKind,
    InjectedDocument,
    PlantedError,
    load_injected,
)

CaughtBy = Literal["check", "schema"]
# Net above gross is one error with two suspects: a flag on either catches it.
PLANTED_ALSO = {"net_over_gross": {"gross_weight_kg"}}
CLEAN_DEV_NAME = "synthetic_clean dev ground truth"
INJECTED_SOURCES_NAME = "injected set, before planting"


class InjectedOutcome(BaseModel):
    id: str
    error: PlantedError
    caught_by: CaughtBy | None  # None: the planted field passed every check
    other_flags: list[str]  # fields flagged besides the planted one


class KindSummary(BaseModel):
    kind: ErrorKind
    documents: int
    caught_by_check: int
    caught_by_schema: int
    with_other_flags: int

    @computed_field
    @property
    def catch_rate(self) -> float | None:
        return ratio(self.caught_by_check + self.caught_by_schema, self.documents)


class CleanSummary(BaseModel):
    name: str
    documents: int
    flagged: int  # documents with any failed check: every one is a false alarm
    alarms_by_rule: dict[str, int]

    @computed_field
    @property
    def false_alarm_rate(self) -> float | None:
        return ratio(self.flagged, self.documents)


class GuardrailReport(BaseModel):
    run_id: str
    created_at: datetime
    git_sha: str | None
    git_dirty: bool | None
    as_of: date
    injected_seed: int
    kinds: list[KindSummary]
    # Every injected document: the set is outside every split, so nothing here is tuned on
    # by accident and the details can be shown.
    outcomes: list[InjectedOutcome]
    clean: list[CleanSummary]


def location_path(location: tuple[int | str, ...]) -> str:
    """Pydantic's ("line_items", 2, "amount") as the pipeline's "line_items[2].amount"."""
    path = ""
    for part in location:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else part
    return path


def evaluate_injected(document: InjectedDocument, as_of: date) -> InjectedOutcome:
    targets = {document.error.path} | PLANTED_ALSO.get(document.error.kind, set())
    # As in the pipeline: exact code synonyms are mapped first, so they are never errors.
    data, _ = map_code_synonyms(document.invoice)
    try:
        invoice = RawInvoice.model_validate(data)
    except ValidationError as error:
        rejected = {location_path(detail["loc"]) for detail in error.errors()}
        caught: CaughtBy | None = "schema" if rejected & targets else None
        return InjectedOutcome(
            id=document.id,
            error=document.error,
            caught_by=caught,
            other_flags=sorted(rejected - targets),
        )
    flagged = {check.path for check in verify_invoice(invoice, as_of).checks}
    return InjectedOutcome(
        id=document.id,
        error=document.error,
        caught_by="check" if flagged & targets else None,
        other_flags=sorted(flagged - targets),
    )


def summarise_kinds(outcomes: list[InjectedOutcome]) -> list[KindSummary]:
    summaries = []
    for kind in ERROR_KINDS:
        of_kind = [outcome for outcome in outcomes if outcome.error.kind == kind]
        summaries.append(
            KindSummary(
                kind=kind,
                documents=len(of_kind),
                caught_by_check=sum(outcome.caught_by == "check" for outcome in of_kind),
                caught_by_schema=sum(outcome.caught_by == "schema" for outcome in of_kind),
                with_other_flags=sum(bool(outcome.other_flags) for outcome in of_kind),
            )
        )
    return summaries


def clean_summary(name: str, invoices: list[RawInvoice], as_of: date) -> CleanSummary:
    """Ground truth is right by definition, so any failed check on it is a false alarm."""
    checks = [verify_invoice(invoice, as_of).checks for invoice in invoices]
    return CleanSummary(
        name=name,
        documents=len(invoices),
        flagged=sum(bool(document_checks) for document_checks in checks),
        alarms_by_rule=dict(Counter(check.rule for document in checks for check in document)),
    )


def build_guardrail_report(
    documents: list[InjectedDocument],
    seed: int,
    as_of: date,
    clean_sets: dict[str, list[RawInvoice]],
) -> GuardrailReport:
    started = datetime.now(UTC)
    sha, dirty = git_state()
    outcomes = [evaluate_injected(document, as_of) for document in documents]
    return GuardrailReport(
        run_id=f"{started:%Y%m%dT%H%M%S}_guardrails",
        created_at=started,
        git_sha=sha,
        git_dirty=dirty,
        as_of=as_of,
        injected_seed=seed,
        kinds=summarise_kinds(outcomes),
        outcomes=outcomes,
        clean=[clean_summary(name, invoices, as_of) for name, invoices in clean_sets.items()],
    )


# --- markdown ------------------------------------------------------------------------------


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _kind_table(report: GuardrailReport) -> list[str]:
    lines = [
        "## Catch rate on the injected set",
        "",
        "Caught: the planted field itself was flagged, by a check or by the extraction "
        "schema. A flag on another field only doesn't count.",
        "",
        "| Error | Documents | Caught by a check | Caught by the schema | Missed | Catch rate "
        "| Other fields flagged too |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for kind in report.kinds:
        caught = kind.caught_by_check + kind.caught_by_schema
        lines.append(
            f"| `{kind.kind}` | {kind.documents} | {kind.caught_by_check} | "
            f"{kind.caught_by_schema} | {kind.documents - caught} | {_percent(kind.catch_rate)} "
            f"| {kind.with_other_flags} |"
        )
    total = sum(kind.documents for kind in report.kinds)
    caught = sum(kind.caught_by_check + kind.caught_by_schema for kind in report.kinds)
    lines += [
        f"| **All** | {total} | | | {total - caught} | **{_percent(ratio(caught, total))}** | |",
        "",
    ]
    return lines


def _misses(report: GuardrailReport) -> list[str]:
    missed = [outcome for outcome in report.outcomes if outcome.caught_by is None]
    if not missed:
        return ["## Misses", "", "None: every planted error was flagged on its own field.", ""]
    lines = [
        "## Misses",
        "",
        "| Document | Error | Field | Original | Planted | How | Other fields flagged |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for outcome in missed:
        error = outcome.error
        others = ", ".join(f"`{path}`" for path in outcome.other_flags) or "none"
        lines.append(
            f"| {outcome.id} | `{error.kind}` | `{error.path}` | {error.original} | "
            f"{error.planted} | {error.method} | {others} |"
        )
    return [*lines, ""]


def _clean_table(report: GuardrailReport) -> list[str]:
    lines = [
        "## False alarms on clean ground truth",
        "",
        "A correct invoice should pass every check, so every flagged document is a false alarm.",
        "",
        "| Set | Documents | Flagged | False-alarm rate | Alarms by rule |",
        "| --- | --- | --- | --- | --- |",
    ]
    for clean in report.clean:
        rules = ", ".join(f"{rule} {count}" for rule, count in sorted(clean.alarms_by_rule.items()))
        lines.append(
            f"| {clean.name} | {clean.documents} | {clean.flagged} | "
            f"{_percent(clean.false_alarm_rate)} | {rules or 'none'} |"
        )
    return [*lines, ""]


def render_guardrail_markdown(report: GuardrailReport) -> str:
    git = report.git_sha[:10] if report.git_sha else "unknown"
    dirty = " (uncommitted changes)" if report.git_dirty else ""
    header = [
        f"# Guardrail eval {report.run_id}",
        "",
        f"- Git: `{git}{dirty}`, run at {report.created_at:%Y-%m-%d %H:%M} UTC; no LLM calls",
        f"- Dates judged as of {report.as_of}; injected set seed {report.injected_seed}",
        "- Layouts don't apply: no page is read, so there is no seen/unseen split here.",
        "",
    ]
    sections = [header, _kind_table(report), _misses(report), _clean_table(report)]
    return "\n".join(line for section in sections for line in section)


def write_guardrail_report(report: GuardrailReport, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    markdown = reports_dir / f"{report.run_id}.md"
    markdown.write_text(render_guardrail_markdown(report), encoding="utf-8")
    (reports_dir / f"{report.run_id}.json").write_bytes(report.model_dump_json(indent=2).encode())
    return markdown


def main() -> None:
    settings = Settings()
    manifest, documents = load_injected(settings.data_dir / INJECTED_DIR)
    # Ground truth only (no extraction): dev, where thresholds may be looked at freely.
    dev = load_documents(settings.data_dir, "synthetic_clean", "dev")
    clean_sets = {
        INJECTED_SOURCES_NAME: [document.source for document in documents],
        CLEAN_DEV_NAME: [document.truth.invoice for document in dev],
    }
    report = build_guardrail_report(documents, manifest.seed, manifest.as_of, clean_sets)
    path = write_guardrail_report(report, settings.reports_dir)
    print(f"Report: {path}")
    for kind in report.kinds:
        print(f"  {kind.kind:18} caught {_percent(kind.catch_rate):>6} of {kind.documents}")
    for clean in report.clean:
        print(f"  {clean.name}: {clean.flagged}/{clean.documents} flagged")


if __name__ == "__main__":
    main()
