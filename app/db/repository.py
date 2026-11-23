"""Database operations for the review workflow. Callers own the session and transaction."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.db.models import (
    Correction,
    Document,
    ExtractionRun,
    HSSuggestionRecord,
    TariffCode,
)
from app.pipeline.hs_suggest import HSLineResult
from app.pipeline.review import DocumentStatus
from app.schemas.result import PipelineResult

# Oldest waiting document first; SKIP LOCKED lets several workers claim different ones.
# Returns what was asked for: a full run (queued) or an HS re-run (hs_queued).
CLAIM_NEXT = text(
    """
    WITH next AS (
        SELECT id, status FROM documents
        WHERE status IN ('queued', 'hs_queued')
        ORDER BY updated_at, id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    UPDATE documents SET status = 'processing', updated_at = :now
    FROM next WHERE documents.id = next.id
    RETURNING documents.id, next.status
    """
)


def now() -> datetime:
    return datetime.now(UTC)


def get_or_create_document(
    session: Session, sha256: str, filename: str, media_type: str, size_bytes: int
) -> tuple[Document, bool]:
    """Idempotent upload: the same bytes return the existing document (design rule 7)."""
    existing = session.get(Document, sha256)
    if existing is not None:
        return existing, False
    stamp = now()
    document = Document(
        id=sha256,
        filename=filename,
        media_type=media_type,
        size_bytes=size_bytes,
        status="queued",
        retryable=False,
        created_at=stamp,
        updated_at=stamp,
    )
    session.add(document)
    return document, True


def list_documents(session: Session, limit: int, offset: int) -> tuple[list[Document], int]:
    total = session.scalar(select(func.count()).select_from(Document)) or 0
    documents = session.scalars(
        select(Document)
        .order_by(Document.created_at.desc(), Document.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return list(documents), total


def claim_next_document(session: Session) -> tuple[str, DocumentStatus] | None:
    """The claimed document's id and the status it was claimed from."""
    row = session.execute(CLAIM_NEXT, {"now": now()}).first()
    return None if row is None else (row[0], row[1])


def set_status(
    session: Session,
    document_id: str,
    status: DocumentStatus,
    error: str | None = None,
    retryable: bool = False,
) -> None:
    session.execute(
        update(Document)
        .where(Document.id == document_id)
        .values(status=status, error=error, retryable=retryable, updated_at=now())
    )


def save_run(session: Session, result: PipelineResult) -> ExtractionRun:
    calls = result.llm_calls
    costs = [call.cost_usd for call in calls]
    run = ExtractionRun(
        document_id=result.document_sha256,
        model=result.model,
        prompt_version=result.prompt_version,
        schema_version=result.schema_version,
        result=result.model_dump(mode="json"),
        cost_usd=None if any(cost is None for cost in costs) else sum(costs, start=0),
        latency_ms=sum(call.latency_ms for call in calls) if calls else None,
        created_at=now(),
    )
    session.add(run)
    session.flush()
    session.execute(
        update(Document)
        .where(Document.id == result.document_sha256)
        .values(page_count=result.page_count)
    )
    return run


def save_hs_line(
    session: Session,
    run_id: int,
    line_index: int,
    line: HSLineResult,
    model: str,
    query_prompt_version: str,
    rerank_prompt_version: str,
) -> None:
    session.add(
        HSSuggestionRecord(
            run_id=run_id,
            line_index=line_index,
            model=model,
            query_prompt_version=query_prompt_version,
            rerank_prompt_version=rerank_prompt_version,
            result=line.model_dump(mode="json"),
        )
    )


def replace_hs_line(
    record: HSSuggestionRecord,
    line: HSLineResult,
    model: str,
    query_prompt_version: str,
    rerank_prompt_version: str,
) -> None:
    """A re-run replaces the line's result; any earlier choice is cleared with it."""
    record.result = line.model_dump(mode="json")
    record.model = model
    record.query_prompt_version = query_prompt_version
    record.rerank_prompt_version = rerank_prompt_version
    record.chosen_code = None


def latest_run(session: Session, document_id: str) -> ExtractionRun | None:
    return session.scalars(
        select(ExtractionRun)
        .where(ExtractionRun.document_id == document_id)
        .order_by(ExtractionRun.id.desc())
        .limit(1)
    ).first()


def hs_lines(session: Session, run_id: int) -> list[HSSuggestionRecord]:
    return list(
        session.scalars(
            select(HSSuggestionRecord)
            .where(HSSuggestionRecord.run_id == run_id)
            .order_by(HSSuggestionRecord.line_index)
        )
    )


def corrections_for(session: Session, run_id: int) -> list[Correction]:
    return list(
        session.scalars(
            select(Correction).where(Correction.run_id == run_id).order_by(Correction.id)
        )
    )


def add_correction(
    session: Session, run_id: int, field_path: str, old_value: Any, new_value: Any
) -> None:
    session.add(
        Correction(
            run_id=run_id,
            field_path=field_path,
            old_value=old_value,
            new_value=new_value,
            created_at=now(),
        )
    )


def tariff_code_exists(session: Session, code: str) -> bool:
    return session.get(TariffCode, code) is not None


def approve(document: Document) -> None:
    stamp = now()
    document.status = "approved"
    document.approved_at = stamp
    document.updated_at = stamp
