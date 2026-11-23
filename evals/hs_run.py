"""CLI: measure HS suggestions (query rewrite, fused retrieval, rerank) on hs_gold.

uv run python -m evals.hs_run --split dev
uv run python -m evals.hs_run --split test   # reporting only, never tuning

Needs the tariff loaded in Postgres (`python -m tariff.ingest`). Stops at the first rate
limit and resumes from the LLM cache next time; `--offline` answers from the cache only.
"""

import argparse
import logging
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from app.config import Settings
from app.db.session import make_engine
from app.db.tariff_index import PgVectorTariffIndex
from app.embeddings import SentenceTransformerEmbedder
from app.llm.factory import build_hs_client
from app.pipeline.hs_suggest import RETRIEVAL_LIMIT, HSLineResult, classify_line
from app.pipeline.prompts import QUERY_TASK, RERANK_TASK, load_task_prompt
from evals.hs_metrics import (
    LEVELS,
    HSGoldItem,
    HSItemOutcome,
    HSSplit,
    HSSummary,
    Rate,
    load_hs_gold,
    summarise_hs,
)
from evals.run import UNSAFE_FILENAME_CHARACTERS, git_state

HS_GOLD_PATH = Path("evals/datasets/hs_gold.jsonl")
TARIFF_VERSION = "2026-27"
# Per-item details only where tuning is allowed, as in the extraction reports.
DETAIL_SPLITS: tuple[HSSplit, ...] = ("dev",)
SHOWN_CANDIDATES = 5


class HSRunMeta(BaseModel):
    run_id: str
    created_at: datetime
    git_sha: str | None
    git_dirty: bool | None
    split: HSSplit
    model: str
    reasoning_effort: str | None
    prompt_version: str  # hs_rerank
    query_prompt_version: str  # hs_query
    embedding_model: str
    tariff_version: str
    items_total: int
    items_scored: int
    gold_verified: int
    llm_calls_made: int
    llm_calls_cached: int


class HSReport(BaseModel):
    meta: HSRunMeta
    summary: HSSummary
    outcomes: list[HSItemOutcome] | None  # DETAIL_SPLITS only


def outcome_for(item: HSGoldItem, line: HSLineResult) -> HSItemOutcome:
    result = line.hs
    top = result.suggestions[0].similarity if result.suggestions else None
    return HSItemOutcome(
        id=item.id,
        gold_code=item.gold_code,
        status=result.stage.status,
        candidates=[candidate.code for candidate in result.candidates],
        suggestions=[suggestion.code for suggestion in result.suggestions],
        top_similarity=top,
        calls=result.calls,
        query_status=line.query.stage.status,
        query_calls=line.query.calls,
    )


def _percent(rate: Rate) -> str:
    value = rate.value
    return "—" if value is None else f"{value:.1%} ({rate.hits}/{rate.total})"


def render_markdown(report: HSReport, items: dict[str, HSGoldItem]) -> str:
    meta, summary = report.meta, report.summary
    git = "unknown" if meta.git_sha is None else meta.git_sha[:10]
    if meta.git_dirty:
        git += " (uncommitted changes)"
    lines = [
        f"# HS eval {meta.run_id}",
        "",
        f"- Git: `{git}`, run at {meta.created_at:%Y-%m-%d %H:%M} UTC",
        f"- Model `{meta.model}` (reasoning {meta.reasoning_effort or 'default'}), prompts "
        f"`hs_query/{meta.query_prompt_version}` and `hs_rerank/{meta.prompt_version}`; "
        f"retrieval `{meta.embedding_model}` vector + full-text over PCT "
        f"{meta.tariff_version}, description and rewrite fused, top {RETRIEVAL_LIMIT}",
        f"- hs_gold `{meta.split}`: {meta.items_scored}/{meta.items_total} items scored; "
        f"gold codes verified by a person: {meta.gold_verified}/{meta.items_total}",
        f"- LLM calls: {meta.llm_calls_made} made, {meta.llm_calls_cached} from cache",
        "",
        "## Accuracy by level (every item counts; needs_review and failed are misses)",
        "",
        "| Measure | Heading (4) | Subheading (6) | National (8) |",
        "| --- | --- | --- | --- |",
    ]
    rows = (
        ("Retrieval recall@10", summary.retrieval_recall),
        ("Retrieval order top-1", summary.retrieval_top1),
        ("Retrieval order top-3", summary.retrieval_top3),
        ("**Rerank top-1**", summary.rerank_top1),
        ("**Rerank top-3**", summary.rerank_top3),
    )
    for name, rates in rows:
        lines.append(f"| {name} | " + " | ".join(_percent(rates[level]) for level in LEVELS) + " |")
    statuses = ", ".join(f"{status} {count}" for status, count in sorted(summary.statuses.items()))
    lines += [
        "",
        "## Operations",
        "",
        f"- Stage outcomes: {statuses}",
        f"- Query rewrite ok: {_percent(summary.query_ok)}",
        f"- Rerank first-try valid answer: {_percent(summary.first_try_valid)}; "
        f"retries {summary.retries}",
        f"- Tokens: {summary.input_tokens if summary.input_tokens is not None else 'unknown'} in, "
        f"{summary.output_tokens if summary.output_tokens is not None else 'unknown'} out",
        f"- Model call time p50 {_seconds(summary.call_ms_p50)}, "
        f"p95 {_seconds(summary.call_ms_p95)}",
        "",
        "## Calibration: national top-1 by retrieval similarity of the first suggestion",
        "",
        "| Similarity | Items | National top-1 |",
        "| --- | --- | --- |",
    ]
    for bucket in summary.calibration:
        lines.append(
            f"| {bucket.low:.2f} to {min(bucket.high, 1):.2f} | {bucket.national_top1.total} | "
            f"{_percent(bucket.national_top1)} |"
        )
    if report.outcomes is not None:
        lines += ["", "## Misses at national level (dev only)", ""]
        misses = [
            o for o in report.outcomes if not (o.suggestions and o.suggestions[0] == o.gold_code)
        ]
        for outcome in misses:
            item = items[outcome.id]
            shown = ", ".join(outcome.candidates[:SHOWN_CANDIDATES])
            lines += [
                f"- **{outcome.id}** {item.description!r}: gold `{outcome.gold_code}`, "
                f"{outcome.status}, suggested {outcome.suggestions or '—'}; "
                f"retrieved top {SHOWN_CANDIDATES}: {shown}"
                + ("" if outcome.gold_code in outcome.candidates else " (gold not retrieved)"),
            ]
        if not misses:
            lines.append("None.")
    return "\n".join(lines) + "\n"


def _seconds(ms: int | None) -> str:
    return "—" if ms is None else f"{ms / 1000:.1f} s"


def write_report(report: HSReport, items: dict[str, HSGoldItem], reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{report.meta.run_id}.md"
    path.write_text(render_markdown(report, items), encoding="utf-8")
    path.with_suffix(".json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Measure HS suggestions on hs_gold.")
    parser.add_argument("--split", choices=["dev", "test"], required=True)
    parser.add_argument("--prompt", default="v1", help="hs_rerank prompt version")
    parser.add_argument("--query-prompt", default="v1", help="hs_query prompt version")
    parser.add_argument("--limit", type=int, help="score only the first N items")
    parser.add_argument("--offline", action="store_true", help="answer from the LLM cache only")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    started = datetime.now(UTC)
    settings = Settings()
    items = load_hs_gold(HS_GOLD_PATH, args.split)[: args.limit]
    embedder = SentenceTransformerEmbedder(settings.embedding_model)
    index = PgVectorTariffIndex(make_engine(settings), settings.embedding_model)
    client = build_hs_client(settings, offline=args.offline)
    prompt = load_task_prompt(RERANK_TASK, args.prompt)
    query_prompt = load_task_prompt(QUERY_TASK, args.query_prompt)
    outcomes: list[HSItemOutcome] = []
    for item in items:
        line = classify_line(item.description, embedder, index, query_prompt, prompt, client)
        if line.hs.stage.status == "failed" and line.hs.stage.retryable:
            print(f"Stopped at {item.id}: {line.hs.stage.error}")
            break
        outcomes.append(outcome_for(item, line))
    sha, dirty = git_state()
    calls = [call for o in outcomes for call in [*o.query_calls, *o.calls]]
    effort = settings.hs_provider_config().reasoning_effort
    model_slug = UNSAFE_FILENAME_CHARACTERS.sub("_", client.model).strip("_")
    parts = [f"{started:%Y%m%dT%H%M%S}", "hs_gold", args.split, model_slug]
    if effort is not None:
        parts.append(f"reasoning-{effort}")
    meta = HSRunMeta(
        run_id="_".join([*parts, f"query-{query_prompt.version}", f"rerank-{prompt.version}"]),
        created_at=started,
        git_sha=sha,
        git_dirty=dirty,
        split=args.split,
        model=client.model,
        reasoning_effort=effort,
        prompt_version=prompt.version,
        query_prompt_version=query_prompt.version,
        embedding_model=settings.embedding_model,
        tariff_version=TARIFF_VERSION,
        items_total=len(items),
        items_scored=len(outcomes),
        gold_verified=sum(item.verified for item in items),
        llm_calls_made=sum(not call.cached for call in calls),
        llm_calls_cached=sum(call.cached for call in calls),
    )
    report = HSReport(
        meta=meta,
        summary=summarise_hs(outcomes),
        outcomes=outcomes if args.split in DETAIL_SPLITS else None,
    )
    path = write_report(report, {item.id: item for item in items}, settings.reports_dir)
    complete = "" if len(outcomes) == len(items) else "  (INCOMPLETE: rerun later to finish)"
    print(f"Report: {path}\nScored {len(outcomes)}/{len(items)} items{complete}")
    for name, rates in (
        ("top-1", report.summary.rerank_top1),
        ("top-3", report.summary.rerank_top3),
    ):
        print(f"  rerank {name}: " + ", ".join(f"{lvl} {_percent(rates[lvl])}" for lvl in LEVELS))


if __name__ == "__main__":
    main()
