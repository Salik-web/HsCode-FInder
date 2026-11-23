"""Worker: process queued documents one at a time. One process, one thread (PDFium).

uv run python -m app.worker          # poll forever
uv run python -m app.worker --once   # process the queue, then exit

Scale by running more worker processes; SKIP LOCKED gives each a different document.
"""

import argparse
import logging
import time
from dataclasses import dataclass
from datetime import date

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import repository
from app.db.models import TariffCode
from app.db.session import make_engine
from app.db.tariff_index import PgVectorTariffIndex
from app.embeddings import SentenceTransformerEmbedder
from app.llm.client import LLMClient
from app.llm.factory import build_client, build_hs_client
from app.pipeline.hs_suggest import Embedder, HSLineResult, TariffIndex, classify_line
from app.pipeline.prompts import QUERY_TASK, RERANK_TASK, TaskPrompt, load_task_prompt
from app.pipeline.review import status_after_run
from app.pipeline.run import PipelineConfig, complete_pipeline, preprocess_stage
from app.pipeline.validate import HSCodeTable
from app.schemas.result import PipelineResult
from app.storage import DocumentStorage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerDeps:
    engine: Engine
    storage: DocumentStorage
    extract_client: LLMClient
    hs_client: LLMClient
    embedder: Embedder
    index: TariffIndex
    hs_codes: HSCodeTable
    invoice_prompt_version: str
    query_prompt: TaskPrompt
    rerank_prompt: TaskPrompt


def build_deps(settings: Settings) -> WorkerDeps:
    engine = make_engine(settings)
    with Session(engine) as session:
        hs_codes = HSCodeTable.from_codes(session.scalars(select(TariffCode.code)))
    return WorkerDeps(
        engine=engine,
        storage=DocumentStorage(settings.storage_dir),
        extract_client=build_client(settings, use_fallback=True),
        hs_client=build_hs_client(settings),
        embedder=SentenceTransformerEmbedder(settings.embedding_model),
        index=PgVectorTariffIndex(engine, settings.embedding_model),
        hs_codes=hs_codes,
        invoice_prompt_version=settings.invoice_prompt_version,
        query_prompt=load_task_prompt(QUERY_TASK, settings.hs_query_prompt_version),
        rerank_prompt=load_task_prompt(RERANK_TASK, settings.hs_rerank_prompt_version),
    )


def process_next(deps: WorkerDeps, today: date) -> str | None:
    """Claim and process one queued document; its id, or None when the queue is empty."""
    with Session(deps.engine) as session, session.begin():
        document_id = repository.claim_next_document(session)
    if document_id is None:
        return None
    try:
        result, hs = _run(deps, document_id, today)
    except Exception as error:  # stage boundary: any failure becomes the document's status
        logger.exception("processing failed", extra={"document_id": document_id})
        with Session(deps.engine) as session, session.begin():
            repository.set_status(session, document_id, "failed", error=type(error).__name__)
        return document_id
    with Session(deps.engine) as session, session.begin():
        run = repository.save_run(session, result)
        for line_index, line in hs.items():
            repository.save_hs_line(
                session,
                run.id,
                line_index,
                line,
                model=deps.hs_client.model,
                query_prompt_version=deps.query_prompt.version,
                rerank_prompt_version=deps.rerank_prompt.version,
            )
        failed_stage = result.extract if result.extract.status == "failed" else result.preprocess
        repository.set_status(
            session,
            document_id,
            status_after_run(result, list(hs.values())),
            error=failed_stage.error,
            retryable=failed_stage.retryable,
        )
    return document_id


def _run(
    deps: WorkerDeps, document_id: str, today: date
) -> tuple[PipelineResult, dict[int, HSLineResult]]:
    preprocessed = preprocess_stage(deps.storage.read_original(document_id))
    if preprocessed.document is not None:
        deps.storage.save_pages(document_id, preprocessed.document.pages)
    config = PipelineConfig(
        prompt_version=deps.invoice_prompt_version, as_of=today, hs_codes=deps.hs_codes
    )
    result = complete_pipeline(preprocessed, config, deps.extract_client)
    hs: dict[int, HSLineResult] = {}
    lines = [] if result.invoice is None else result.invoice.line_items
    for index, line in enumerate(lines):
        if line.description:  # no description, nothing to classify
            hs[index] = classify_line(
                line.description,
                deps.embedder,
                deps.index,
                deps.query_prompt,
                deps.rerank_prompt,
                deps.hs_client,
            )
    return result, hs


def main() -> None:
    parser = argparse.ArgumentParser(description="Process queued documents.")
    parser.add_argument("--once", action="store_true", help="exit when the queue is empty")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    settings = Settings()
    deps = build_deps(settings)
    while True:
        processed = process_next(deps, date.today())
        if processed is not None:
            logger.info("processed", extra={"document_id": processed})
            continue
        if args.once:
            return
        time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
