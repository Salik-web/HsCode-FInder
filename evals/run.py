"""CLI: run the pipeline on a labelled dataset split and write a report.

uv run python -m evals.run --dataset synthetic_clean --split dev --limit 20

Free tiers allow only some calls a day, so a run can stop part-way: documents the LLM
couldn't reach are `not_scored`, the report says the run is incomplete, and rerunning the
same command later scores the rest while the cache answers the rest for free.
"""

import argparse
import logging
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

from app.config import Settings
from app.llm.client import LLMClient
from app.llm.factory import build_client
from app.pipeline.run import PipelineConfig, Preprocessed, complete_pipeline, preprocess_stage
from app.schemas.constants import SCHEMA_VERSION
from app.schemas.result import PipelineResult
from evals.dataset import EvalDocument, load_documents
from evals.metrics import score_document
from evals.report import (
    DocumentRecord,
    EvalReport,
    RunMetadata,
    build_report,
    find_previous,
    write_report,
)
from synth.manifest import Dataset, Split

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_VERSION = "v1"
UNSAFE_FILENAME_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]+")


class RunConfig(PipelineConfig):
    dataset: Dataset
    split: Split
    model: str


def run_eval(
    documents: list[EvalDocument],
    client: LLMClient,
    config: RunConfig,
    reports_dir: Path,
    concurrency: int,
) -> tuple[EvalReport, Path]:
    """Score every document, then write the report next to earlier ones."""
    started = datetime.now(UTC)
    # PDFium isn't thread-safe, so every file is rendered here, on one thread.
    preprocessed = [preprocess_stage(document.read_verified_bytes()) for document in documents]
    results = _run_llm_stages(preprocessed, client, config, concurrency)
    records = [
        _record(document, result) for document, result in zip(documents, results, strict=True)
    ]
    meta = _metadata(config, records, started)
    report = build_report(meta, records)
    previous = find_previous(reports_dir, meta) if reports_dir.exists() else None
    return report, write_report(report, reports_dir, previous)


def _run_llm_stages(
    preprocessed: list[Preprocessed], client: LLMClient, config: RunConfig, concurrency: int
) -> list[PipelineResult | None]:
    """Run the LLM stages concurrently; after one quota error, start no new documents.

    A daily quota won't recover within the run, so continuing would only spend each
    remaining document's retries. Those documents return None and count as not_scored.
    """
    quota_hit = threading.Event()

    def process(item: Preprocessed) -> PipelineResult | None:
        if quota_hit.is_set():
            return None
        result = complete_pipeline(item, config, client)
        if result.extract.status == "failed" and result.extract.retryable:
            quota_hit.set()
            logger.warning("LLM unavailable, stopping new documents: %s", result.extract.error)
        return result

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        return list(pool.map(process, preprocessed))


def _record(document: EvalDocument, result: PipelineResult | None) -> DocumentRecord:
    entry = document.entry
    base = {
        "id": entry.id,
        "template": entry.template,
        "layout_group": entry.layout_group,
        "buyer_form": document.buyer_form,
    }
    if result is None:
        return DocumentRecord(
            **base,
            status="not_scored",
            preprocess_status="ok",
            extract_status="not_run",
            error="skipped after the LLM became unavailable",
            score=None,
            llm_calls=[],
        )
    retryable = result.extract.status == "failed" and result.extract.retryable
    # A bad file or output that never validated is the pipeline's failure: it scores as
    # no_output. Only a temporary LLM outage leaves a document unscored.
    score = None if retryable else score_document(document.truth.invoice, result.invoice)
    return DocumentRecord(
        **base,
        status="not_scored" if retryable else "scored",
        preprocess_status=result.preprocess.status,
        extract_status=result.extract.status,
        error=result.preprocess.error or result.extract.error,
        score=score,
        llm_calls=result.llm_calls,
    )


def _git_state() -> tuple[str | None, bool | None]:
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=10
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True, timeout=10
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None, None
    return sha, bool(status.strip())


def _metadata(config: RunConfig, records: list[DocumentRecord], started: datetime) -> RunMetadata:
    sha, dirty = _git_state()
    calls = [call for record in records for call in record.llm_calls]
    model_slug = UNSAFE_FILENAME_CHARACTERS.sub("_", config.model).strip("_")
    parts = [f"{started:%Y%m%dT%H%M%S}", config.dataset, config.split, model_slug]
    run_id = "_".join([*parts, config.prompt_version])
    return RunMetadata(
        run_id=run_id,
        created_at=started,
        git_sha=sha,
        git_dirty=dirty,
        dataset=config.dataset,
        split=config.split,
        model=config.model,
        prompt_version=config.prompt_version,
        schema_version=SCHEMA_VERSION,
        documents_total=len(records),
        documents_scored=sum(record.status == "scored" for record in records),
        llm_calls_made=sum(not call.cached for call in calls),
        llm_calls_cached=sum(call.cached for call in calls),
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Score the pipeline on a labelled dataset.")
    parser.add_argument("--dataset", choices=get_args(Dataset), required=True)
    parser.add_argument("--split", choices=get_args(Split), required=True)
    parser.add_argument("--limit", type=int, help="score only the first N documents")
    parser.add_argument("--model", help="override LLM_MODEL from the environment")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT_VERSION, help="prompt version")
    parser.add_argument(
        "--fallback",
        action="store_true",
        help="let the fallback model answer when the primary is rate-limited "
        "(off by default, so one run measures one model)",
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    return args


def _print_summary(report: EvalReport, path: Path) -> None:
    meta = report.meta
    print(f"Report: {path}")
    print(f"Scored {meta.documents_scored}/{meta.documents_total} documents", end="")
    print("" if meta.complete else "  (INCOMPLETE: rerun later to finish)")
    for group, sliced in report.layout_groups.items():
        summary = sliced.summary
        if summary.documents == 0:
            continue
        critical = summary.critical_field_accuracy
        all_right = summary.all_critical_correct_rate
        print(
            f"  {group:6} n={summary.documents:3}  critical-field accuracy "
            f"{'—' if critical is None else f'{critical:.1%}'}  all critical right "
            f"{'—' if all_right is None else f'{all_right:.1%}'}"
        )


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings()
    if args.model:
        settings = settings.model_copy(update={"llm_model": args.model})
    documents = load_documents(settings.data_dir, args.dataset, args.split, args.limit)
    client = build_client(settings, use_fallback=args.fallback)
    config = RunConfig(
        prompt_version=args.prompt, dataset=args.dataset, split=args.split, model=client.model
    )
    report, path = run_eval(
        documents, client, config, settings.reports_dir, settings.llm_concurrency
    )
    _print_summary(report, path)


if __name__ == "__main__":
    main()
