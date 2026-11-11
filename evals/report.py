"""Eval reports: slice scores, render markdown, diff against the previous run.

Rules the code enforces, not just documents:
- Seen and unseen layouts are always reported separately, never blended.
- Every number carries its document count.
- Per-document details exist for the dev split only, so test splits can't be tuned on.
"""

import math
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.schemas.result import LLMCall, StageStatus
from evals.dataset import BuyerForm
from evals.metrics import DocumentScore, FieldAccuracy, Summary, accuracy_by_field, summarise
from synth.manifest import LayoutGroup, Split

DETAIL_SPLITS: tuple[Split, ...] = ("dev",)
LAYOUT_GROUPS: tuple[LayoutGroup, ...] = ("seen", "unseen")
BUYER_FORMS: tuple[BuyerForm, ...] = ("reference", "repeated", "distinct")
BUYER_FIELD_PREFIX = "buyer."
WORST_FAILURE_COUNT = 10
MAX_VALUE_CHARS = 60
P50 = 50
P95 = 95

DocumentStatus = Literal["scored", "not_scored"]


class DocumentRecord(BaseModel):
    id: str
    template: str
    layout_group: LayoutGroup
    buyer_form: BuyerForm
    # not_scored: the LLM was rate-limited or unreachable; rerun later, the cache keeps
    # what's done. Never counted as right or wrong.
    status: DocumentStatus
    preprocess_status: StageStatus
    extract_status: StageStatus
    error: str | None
    score: DocumentScore | None
    llm_calls: list[LLMCall]


class OperationalStats(BaseModel):
    documents: int
    needs_review_rate: float | None
    failed_rate: float | None
    retry_rate: float | None
    cost_per_document_usd: Decimal | None  # None when any call's cost is unknown
    latency_p50_ms: int | None
    latency_p95_ms: int | None


class SliceReport(BaseModel):
    summary: Summary
    operations: OperationalStats


class RunMetadata(BaseModel):
    run_id: str
    created_at: datetime
    git_sha: str | None
    git_dirty: bool | None
    dataset: str
    split: Split
    model: str
    # None: the provider's default. Reports from before this field existed have None.
    reasoning_effort: str | None = None
    prompt_version: str
    schema_version: str
    documents_total: int
    documents_scored: int
    llm_calls_made: int  # calls that reached a provider during this run
    llm_calls_cached: int

    @property
    def complete(self) -> bool:
        return self.documents_scored == self.documents_total


class EvalReport(BaseModel):
    meta: RunMetadata
    layout_groups: dict[LayoutGroup, SliceReport]
    templates: dict[str, SliceReport]
    # Buyer fields only, per buyer form and layout group.
    buyer_forms: dict[BuyerForm, dict[LayoutGroup, list[FieldAccuracy]]]
    documents: list[DocumentRecord] | None  # dev only


def percentile(values: list[int], percent: int) -> int | None:
    """Nearest-rank percentile: always a value that was actually observed."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(percent / 100 * len(ordered)) - 1]


def _rate(records: list[DocumentRecord], matches: int) -> float | None:
    return None if not records else matches / len(records)


def operational_stats(records: list[DocumentRecord]) -> OperationalStats:
    """Over scored documents; latency and cost are the original calls', even when cached."""
    latencies = [sum(call.latency_ms for call in r.llm_calls) for r in records if r.llm_calls]
    costs = [call.cost_usd for record in records for call in record.llm_calls]
    known_costs = [cost for cost in costs if cost is not None]
    cost_per_document = None
    if records and len(known_costs) == len(costs):
        cost_per_document = sum(known_costs, Decimal(0)) / len(records)
    return OperationalStats(
        documents=len(records),
        needs_review_rate=_rate(records, sum(r.extract_status == "needs_review" for r in records)),
        failed_rate=_rate(
            records, sum("failed" in (r.preprocess_status, r.extract_status) for r in records)
        ),
        retry_rate=_rate(records, sum(len(r.llm_calls) > 1 for r in records)),
        cost_per_document_usd=cost_per_document,
        latency_p50_ms=percentile(latencies, P50),
        latency_p95_ms=percentile(latencies, P95),
    )


def slice_report(records: list[DocumentRecord]) -> SliceReport:
    scored = [record for record in records if record.score is not None]
    return SliceReport(
        summary=summarise([record.score for record in scored]),
        operations=operational_stats(scored),
    )


def buyer_accuracy(records: list[DocumentRecord]) -> list[FieldAccuracy]:
    fields = (
        field
        for record in records
        if record.score is not None
        for field in record.score.fields
        if field.field.startswith(BUYER_FIELD_PREFIX)
    )
    return accuracy_by_field(fields)


def build_report(meta: RunMetadata, records: list[DocumentRecord]) -> EvalReport:
    def in_group(group: LayoutGroup) -> list[DocumentRecord]:
        return [record for record in records if record.layout_group == group]

    templates = sorted({record.template for record in records})
    return EvalReport(
        meta=meta,
        layout_groups={group: slice_report(in_group(group)) for group in LAYOUT_GROUPS},
        templates={
            template: slice_report([r for r in records if r.template == template])
            for template in templates
        },
        buyer_forms={
            form: {
                group: buyer_accuracy([r for r in in_group(group) if r.buyer_form == form])
                for group in LAYOUT_GROUPS
            }
            for form in BUYER_FORMS
        },
        documents=records if meta.split in DETAIL_SPLITS else None,
    )


# --- files ---------------------------------------------------------------------------------


def write_report(report: EvalReport, reports_dir: Path, previous: EvalReport | None) -> Path:
    """Write <run_id>.json and <run_id>.md; return the markdown path."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / f"{report.meta.run_id}.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )
    markdown_path = reports_dir / f"{report.meta.run_id}.md"
    markdown_path.write_text(render_markdown(report, previous), encoding="utf-8")
    return markdown_path


def find_previous(reports_dir: Path, current: RunMetadata) -> EvalReport | None:
    """The latest earlier run on the same dataset and split, whatever its model or prompt."""
    candidates = []
    for path in reports_dir.glob("*.json"):
        report = EvalReport.model_validate_json(path.read_bytes())
        meta = report.meta
        if (meta.dataset, meta.split) == (current.dataset, current.split) and (
            meta.run_id != current.run_id and meta.created_at < current.created_at
        ):
            candidates.append(report)
    return max(candidates, key=lambda report: report.meta.created_at, default=None)


# --- markdown ------------------------------------------------------------------------------


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _count(accuracy: FieldAccuracy | None) -> str:
    if accuracy is None or accuracy.total == 0:
        return "— (0)"
    return f"{_percent(accuracy.accuracy)} ({accuracy.correct}/{accuracy.total})"


def _ms(value: int | None) -> str:
    return "—" if value is None else f"{value / 1000:.1f} s"


def _usd(value: Decimal | None) -> str:
    return "unknown" if value is None else f"${value:.5f}"


def _cost_cell(sliced: SliceReport) -> str:
    """No documents means no cost to report, which is different from an unknown cost."""
    if sliced.operations.documents == 0:
        return "—"
    return _usd(sliced.operations.cost_per_document_usd)


def _truncate(value: str | None) -> str:
    if value is None:
        return "null"
    text = value if len(value) <= MAX_VALUE_CHARS else value[: MAX_VALUE_CHARS - 1] + "…"
    return text.replace("|", "\\|")


def _header(meta: RunMetadata) -> list[str]:
    git = meta.git_sha[:10] if meta.git_sha else "unknown"
    if meta.git_dirty:
        git += " (uncommitted changes)"
    lines = [
        f"# Eval run {meta.run_id}",
        "",
        f"- Dataset: `{meta.dataset}`, split `{meta.split}`",
        f"- Model: `{meta.model}`, reasoning `{meta.reasoning_effort or 'provider default'}`, "
        f"prompt `{meta.prompt_version}`, schema `{meta.schema_version}`",
        f"- Git: `{git}`, run at {meta.created_at:%Y-%m-%d %H:%M} UTC",
        f"- Documents scored: {meta.documents_scored} of {meta.documents_total}; "
        f"LLM calls made: {meta.llm_calls_made}, from cache: {meta.llm_calls_cached}",
        "",
    ]
    if not meta.complete:
        missing = meta.documents_total - meta.documents_scored
        lines += [
            f"> **INCOMPLETE:** {missing} documents not scored (rate-limited or unreachable). "
            "Rerun the same command later; scored documents come from cache.",
            "",
        ]
    return lines


def _headline_table(report: EvalReport) -> list[str]:
    groups = report.layout_groups

    def row(name: str, render: Callable[[SliceReport], str]) -> str:
        return f"| {name} | " + " | ".join(render(groups[group]) for group in LAYOUT_GROUPS) + " |"

    return [
        "## Headline (seen and unseen layouts are never blended)",
        "",
        "| Metric | Seen | Unseen |",
        "| --- | --- | --- |",
        row("Documents scored", lambda s: str(s.summary.documents)),
        row("Critical-field accuracy", lambda s: _percent(s.summary.critical_field_accuracy)),
        row("All critical fields right", lambda s: _percent(s.summary.all_critical_correct_rate)),
        row("Line precision", lambda s: _percent(s.summary.line_precision)),
        row("Line recall", lambda s: _percent(s.summary.line_recall)),
        row("Line F1", lambda s: _percent(s.summary.line_f1)),
        row("needs_review rate", lambda s: _percent(s.operations.needs_review_rate)),
        row("Failed rate", lambda s: _percent(s.operations.failed_rate)),
        row("Retry rate", lambda s: _percent(s.operations.retry_rate)),
        row("Cost per document", _cost_cell),
        row("Latency p50", lambda s: _ms(s.operations.latency_p50_ms)),
        row("Latency p95", lambda s: _ms(s.operations.latency_p95_ms)),
        "",
    ]


def _field_table(report: EvalReport) -> list[str]:
    by_group = {
        group: {field.field: field for field in report.layout_groups[group].summary.fields}
        for group in LAYOUT_GROUPS
    }
    names = list(dict.fromkeys(name for group in LAYOUT_GROUPS for name in by_group[group]))
    lines = [
        "## Per-field accuracy (correct/total; line fields on matched lines only)",
        "",
        "| Field | Seen | Unseen | Seen missed / hallucinated |",
        "| --- | --- | --- | --- |",
    ]
    for name in names:
        seen = by_group["seen"].get(name)
        unseen = by_group["unseen"].get(name)
        errors = "—" if seen is None else f"{seen.missed} / {seen.hallucinated}"
        lines.append(f"| `{name}` | {_count(seen)} | {_count(unseen)} | {errors} |")
    return [*lines, ""]


def _template_table(report: EvalReport) -> list[str]:
    lines = [
        "## By template",
        "",
        "| Template | Documents | Critical-field accuracy | All critical right | Line F1 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for template, sliced in report.templates.items():
        summary = sliced.summary
        lines.append(
            f"| {template} | {summary.documents} | {_percent(summary.critical_field_accuracy)} "
            f"| {_percent(summary.all_critical_correct_rate)} | {_percent(summary.line_f1)} |"
        )
    return [*lines, ""]


def _buyer_table(report: EvalReport) -> list[str]:
    lines = [
        "## Buyer by how it is printed",
        "",
        '`reference`: the buyer block says e.g. "Same as consignee" and the truth is the '
        "consignee's details. `repeated`: the same company printed in full. `distinct`: a "
        "different company.",
        "",
        "| Buyer form | Field | Seen | Unseen |",
        "| --- | --- | --- | --- |",
    ]
    for form, groups in report.buyer_forms.items():
        seen = {field.field: field for field in groups["seen"]}
        unseen = {field.field: field for field in groups["unseen"]}
        for name in dict.fromkeys([*seen, *unseen]):
            cells = f"{_count(seen.get(name))} | {_count(unseen.get(name))}"
            lines.append(f"| {form} | `{name}` | {cells} |")
    return [*lines, ""]


def _delta(current: float | None, previous: float | None) -> str:
    if current is None or previous is None:
        return "—"
    return f"{(current - previous) * 100:+.1f}"


def _diff(report: EvalReport, previous: EvalReport | None) -> list[str]:
    title = "## Change since the previous run"
    if previous is None:
        return [title, "", "No earlier run on this dataset and split.", ""]
    meta = previous.meta
    lines = [
        title,
        "",
        f"Previous: `{meta.run_id}` (model `{meta.model}`, prompt `{meta.prompt_version}`, "
        f"{meta.documents_scored}/{meta.documents_total} scored). Points of change:",
        "",
        "| Metric | Seen | Unseen |",
        "| --- | --- | --- |",
    ]
    metrics = {
        "Critical-field accuracy": lambda s: s.critical_field_accuracy,
        "All critical right": lambda s: s.all_critical_correct_rate,
        "Line F1": lambda s: s.line_f1,
    }
    for name, read in metrics.items():
        cells = [
            _delta(read(report.layout_groups[g].summary), read(previous.layout_groups[g].summary))
            for g in LAYOUT_GROUPS
        ]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    changed = []
    for group in LAYOUT_GROUPS:
        before = {f.field: f.accuracy for f in previous.layout_groups[group].summary.fields}
        for field in report.layout_groups[group].summary.fields:
            if field.field in before and field.accuracy != before[field.field]:
                delta = _delta(field.accuracy, before[field.field])
                changed.append(f"| {group} | `{field.field}` | {delta} |")
    if changed:
        lines += ["", "| Group | Field | Points |", "| --- | --- | --- |", *changed]
    return [*lines, ""]


def _failure_weight(score: DocumentScore) -> tuple[int, int]:
    """Critical errors first, then all others: how badly one document went wrong."""
    critical_wrong = sum(field.outcome != "correct" for field in score.critical)
    other_wrong = sum(field.outcome != "correct" for field in score.fields)
    return critical_wrong, other_wrong


def _worst_failures(report: EvalReport) -> list[str]:
    if report.documents is None:
        return [
            "## Failures",
            "",
            f"Per-document details are shown for {', '.join(DETAIL_SPLITS)} only, so this "
            "split can't be used for tuning.",
            "",
        ]
    failing = [
        (record, record.score)
        for record in report.documents
        if record.score is not None and any(_failure_weight(record.score))
    ]
    failing.sort(key=lambda pair: _failure_weight(pair[1]), reverse=True)
    worst = failing[:WORST_FAILURE_COUNT]
    lines = [f"## {len(worst)} worst failures (of {len(failing)} documents with any error)", ""]
    for record, score in worst:
        lines += _failure_section(record, score)
    not_scored = [r for r in report.documents if r.status == "not_scored"]
    if not_scored:
        lines += ["### Not scored", "", *[f"- {r.id}: {r.error}" for r in not_scored], ""]
    return lines


def _failure_section(record: DocumentRecord, score: DocumentScore) -> list[str]:
    critical, other = _failure_weight(score)
    lines = [
        f"### {record.id} ({record.template}, buyer {record.buyer_form}, "
        f"extract {record.extract_status})",
        "",
        f"{critical} critical and {other} other fields not correct. "
        f"Lines: {score.lines.gold} true, {score.lines.predicted} predicted, "
        f"{score.lines.matched} matched.",
        "",
        *([f"Error: {record.error}", ""] if record.error else []),
        "| Field | Outcome | Expected | Predicted |",
        "| --- | --- | --- | --- |",
    ]
    # Critical header fields also appear in `fields`; list each distinct error once.
    wrong = [f for f in [*score.fields, *score.critical] if f.outcome != "correct"]
    for field in {(f.field, f.outcome, f.expected, f.predicted): f for f in wrong}.values():
        lines.append(
            f"| `{field.field}` | {field.outcome} | {_truncate(field.expected)} "
            f"| {_truncate(field.predicted)} |"
        )
    return [*lines, ""]


def render_markdown(report: EvalReport, previous: EvalReport | None) -> str:
    sections = [
        _header(report.meta),
        _headline_table(report),
        _field_table(report),
        _template_table(report),
        _buyer_table(report),
        _diff(report, previous),
        _worst_failures(report),
    ]
    return "\n".join(line for section in sections for line in section)
