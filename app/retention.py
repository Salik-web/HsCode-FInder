"""Public-demo retention: uploads are purged, samples reset, cold starts cleaned up.

Uploads are deleted after the retention window (rows and files). A free Space loses its
disk when it restarts, so an upload whose files are gone is purged too. Samples are
never deleted; a visitor's corrections, HS choices and approval on them are undone once
they are older than the window, so every visitor starts from the extracted result.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.db import repository
from app.pipeline.hs_suggest import HSLineResult
from app.pipeline.review import status_after_run
from app.schemas.result import PipelineResult
from app.storage import DocumentStorage

logger = logging.getLogger(__name__)

CLEANUP_INTERVAL_SECONDS = 300
UPLOAD_LOG_DAYS = 2  # the daily caps look back one day; keep a little more


@dataclass(frozen=True)
class CleanupReport:
    purged: int
    orphans: int
    samples_reset: int


def cleanup(
    engine: Engine, storage: DocumentStorage, retention_minutes: int, now: datetime
) -> CleanupReport:
    before = now - timedelta(minutes=retention_minutes)
    with Session(engine) as session, session.begin():
        expired = set(repository.uploads_to_purge(session, before))
        orphans = {
            document_id
            for document_id in repository.uploads(session)
            if document_id not in expired
            and not storage.has_original(document_id)
            and not storage.has_pages(document_id)
        }
        for document_id in expired | orphans:
            repository.delete_document(session, document_id)
        reset = _reset_samples(session, before)
        repository.prune_upload_log(session, now - timedelta(days=UPLOAD_LOG_DAYS))
    for document_id in expired | orphans:
        storage.delete_all(document_id)
    report = CleanupReport(purged=len(expired), orphans=len(orphans), samples_reset=reset)
    logger.info("demo cleanup", extra={"report": report})
    return report


def _reset_samples(session: Session, before: datetime) -> int:
    reset = 0
    for sample in repository.samples(session):
        if sample.updated_at >= before or sample.status in ("queued", "processing"):
            continue
        run = repository.latest_run(session, sample.id)
        if run is None:
            continue
        repository.clear_review(session, run.id)
        lines = [
            HSLineResult.model_validate(r.result) for r in repository.hs_lines(session, run.id)
        ]
        status = status_after_run(PipelineResult.model_validate(run.result), lines)
        repository.set_status(session, sample.id, status)
        sample.approved_at = None
        reset += 1
    return reset
