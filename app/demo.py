"""Public demo: seed the sample invoices, served from a committed LLM cache.

uv run python -m app.demo seed          # idempotent; cache only, never calls an LLM
uv run python -m app.demo seed --live   # once, locally: fill demo/llm_cache with live calls

Samples are synthetic invoices in demo/samples. On a cold start (a free Space restarted
and lost its disk) a sample whose page images are gone is processed again from the
cache, so the database and the disk agree.
"""

import argparse
import dataclasses
import hashlib
import logging
import sys
from datetime import date
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import repository
from app.db.models import Document
from app.llm.factory import build_client, build_hs_client
from app.pipeline.hs_suggest import HSLineResult
from app.pipeline.preprocess import detect_media_type
from app.worker import WorkerDeps, build_deps, process_document

logger = logging.getLogger(__name__)

SAMPLE_SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png")


class SeedError(RuntimeError):
    """A sample couldn't be processed from the cache: re-run `seed --live` and commit."""


def sample_files(samples_dir: Path) -> list[Path]:
    return sorted(path for path in samples_dir.iterdir() if path.suffix.lower() in SAMPLE_SUFFIXES)


def demo_deps(settings: Settings, live: bool) -> WorkerDeps:
    """Worker dependencies whose LLM answers come from the demo cache (and only from it,
    unless `live`). Samples keep their files; HS covers as many lines as for an upload."""
    demo_settings = settings.model_copy(update={"cache_dir": settings.demo_cache_dir})
    deps = build_deps(demo_settings)
    return dataclasses.replace(
        deps,
        extract_client=build_client(demo_settings, use_fallback=False, offline=not live),
        hs_client=build_hs_client(demo_settings, offline=not live),
        hs_line_limit=settings.demo_hs_lines,
        delete_original_after=False,
    )


def seed(settings: Settings, live: bool, today: date) -> list[str]:
    """Make sure every sample is in the database with its pages on disk; their ids."""
    return seed_samples(demo_deps(settings, live), settings.demo_samples_dir, today)


def seed_samples(deps: WorkerDeps, samples_dir: Path, today: date) -> list[str]:
    ids = []
    for path in sample_files(samples_dir):
        data = path.read_bytes()
        document_id = hashlib.sha256(data).hexdigest()
        ids.append(document_id)
        if _is_ready(deps, document_id):
            continue
        with Session(deps.engine) as session, session.begin():
            repository.delete_document(session, document_id)  # a stale row from a lost disk
            repository.get_or_create_document(
                session,
                document_id,
                path.name,
                detect_media_type(data),
                len(data),
                is_sample=True,
            )
            session.execute(
                update(Document).where(Document.id == document_id).values(status="processing")
            )
        deps.storage.save_original(document_id, data)
        process_document(deps, document_id, today)
        _check_processed(deps, document_id, path.name)
    return ids


def _is_ready(deps: WorkerDeps, document_id: str) -> bool:
    """Processed, every HS line finished, and the pages on disk."""
    return _problem(deps, document_id) is None and deps.storage.has_pages(document_id)


def _check_processed(deps: WorkerDeps, document_id: str, name: str) -> None:
    problem = _problem(deps, document_id)
    if problem is not None:
        raise SeedError(f"{name}: {problem} (run `python -m app.demo seed --live`)")


def _problem(deps: WorkerDeps, document_id: str) -> str | None:
    """Why a sample isn't fully served from the cache, or None when it is."""
    with Session(deps.engine) as session:
        document = session.get(Document, document_id)
        if document is None or document.status in ("queued", "processing"):
            return "not processed"
        if document.status == "failed":
            return f"processing failed: {document.error}"
        run = repository.latest_run(session, document_id)
        records = [] if run is None else repository.hs_lines(session, run.id)
        unfinished = [
            record.line_index
            for record in records
            if HSLineResult.model_validate(record.result).hs.stage.status != "ok"
        ]
    return f"HS unfinished on lines {unfinished}" if unfinished else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the demo's sample invoices.")
    parser.add_argument("command", choices=["seed"])
    parser.add_argument("--live", action="store_true", help="call the LLMs to fill the cache")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        ids = seed(Settings(), live=args.live, today=date.today())
    except SeedError as error:
        print(f"Seeding failed: {error}", file=sys.stderr)
        sys.exit(1)
    print(f"Samples ready: {len(ids)}")


if __name__ == "__main__":
    main()
