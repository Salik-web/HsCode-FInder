"""FastAPI app: upload, review, correct, approve, export. Routes only; logic lives elsewhere.

uv run uvicorn app.api.main:create_app --factory --reload

Processing happens in the worker (`python -m app.worker`); the API only stores the file
and queues it. Handlers are sync: FastAPI runs them in its thread pool, so the sync
database driver never blocks the event loop.
"""

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import repository
from app.db.models import Document, ExtractionRun
from app.db.session import make_engine
from app.pipeline.hs_suggest import HSLineResult
from app.pipeline.preprocess import MAX_FILE_BYTES, InvalidDocumentError, detect_media_type
from app.pipeline.review import (
    REVIEWABLE_STATUSES,
    CorrectionInput,
    DocumentStatus,
    InvalidCorrectionError,
    apply_corrections,
    export_csv,
    export_json,
    field_value,
    lines_needing_hs,
    safe_filename,
)
from app.schemas.raw import RawInvoice
from app.schemas.result import PipelineResult
from app.schemas.tariff import PCT_CODE_PATTERN
from app.storage import DocumentStorage, StorageError

DocumentId = Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]
MAX_PAGE_SIZE = 100


class DocumentOut(BaseModel):
    id: str
    filename: str
    media_type: str
    size_bytes: int
    status: DocumentStatus
    error: str | None
    retryable: bool
    page_count: int | None

    @classmethod
    def of(cls, document: Document) -> "DocumentOut":
        return cls.model_validate(document, from_attributes=True)


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    total: int
    limit: int
    offset: int


class HSLineOut(BaseModel):
    line_index: int
    result: dict[str, Any]  # HSLineResult: query and rerank stages, candidates, suggestions
    chosen_code: str | None


class CorrectionOut(BaseModel):
    field_path: str
    old_value: Any
    new_value: Any


class FieldOut(BaseModel):
    """One extracted field as the reviewer sees it."""

    path: str
    value: Any  # after corrections
    extracted: Any  # as the model returned it
    status: str  # ok | needs_review | failed_check, from the run's assessment
    confidence: float
    reasons: list[str]
    corrected: bool


class ReviewOut(BaseModel):
    document: DocumentOut
    run_id: int
    model: str
    prompt_version: str
    result: dict[str, Any]  # PipelineResult as extracted, before corrections
    invoice: dict[str, Any] | None  # with corrections applied
    corrections: list[CorrectionOut]
    fields: list[FieldOut]
    hs_lines: list[HSLineOut]


class HSChoice(BaseModel):
    code: str = Field(pattern=PCT_CODE_PATTERN)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="TradeDoc AI", version="0.1.0")
    app.state.settings = settings
    app.state.engine = make_engine(settings)
    app.state.storage = DocumentStorage(settings.storage_dir)
    _routes(app)
    return app


def get_session(request: Request) -> Iterator[Session]:
    engine: Engine = request.app.state.engine
    with Session(engine) as session, session.begin():
        yield session


def get_storage(request: Request) -> DocumentStorage:
    return request.app.state.storage


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
# The review UI passes the visitor's IP (from the Space's proxy headers) over localhost;
# only a salted hash of it is stored.
VisitorHeader = Annotated[str | None, Header(alias="X-Visitor")]
UNKNOWN_VISITOR = "unknown"
StorageDep = Annotated[DocumentStorage, Depends(get_storage)]


def _routes(app: FastAPI) -> None:
    @app.get("/health")
    def health(session: SessionDep) -> dict[str, str]:
        session.execute(text("SELECT 1"))
        return {"status": "ok"}

    @app.post("/documents", status_code=status.HTTP_202_ACCEPTED)
    def upload(
        file: UploadFile,
        session: SessionDep,
        storage: StorageDep,
        settings: SettingsDep,
        visitor: VisitorHeader = None,
    ) -> DocumentOut:
        data = file.file.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file is over 10 MB")
        try:
            media_type = detect_media_type(data)
        except InvalidDocumentError as error:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(error)) from error
        sha256 = hashlib.sha256(data).hexdigest()
        existing = session.get(Document, sha256)
        if existing is not None:  # the same bytes again: no new work, nothing counted
            return DocumentOut.of(existing)
        uploader = _uploader(visitor, settings) if settings.demo_mode else None
        if settings.demo_mode:
            _check_demo_caps(session, settings, uploader)
        document, created = repository.get_or_create_document(
            session, sha256, safe_filename(file.filename or ""), media_type, len(data), uploader
        )
        if settings.demo_mode:
            repository.log_upload(session, uploader)
        if created:
            storage.save_original(sha256, data)
        return DocumentOut.of(document)

    @app.get("/documents")
    def list_documents(
        session: SessionDep,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> DocumentPage:
        documents, total = repository.list_documents(session, limit, offset)
        items = [DocumentOut.of(document) for document in documents]
        return DocumentPage(items=items, total=total, limit=limit, offset=offset)

    @app.get("/documents/{document_id}")
    def get_document(document_id: DocumentId, session: SessionDep) -> DocumentOut:
        return DocumentOut.of(_document(session, document_id))

    @app.get("/documents/{document_id}/pages/{number}")
    def page_image(
        document_id: DocumentId, number: Annotated[int, Path(ge=1)], storage: StorageDep
    ) -> FileResponse:
        try:
            return FileResponse(storage.page_path(document_id, number))
        except StorageError as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error

    @app.get("/documents/{document_id}/review")
    def review(document_id: DocumentId, session: SessionDep) -> ReviewOut:
        document = _document(session, document_id)
        run = _run(session, document_id)
        corrections = repository.corrections_for(session, run.id)
        invoice = _reviewed_invoice(run, corrections)
        extracted = PipelineResult.model_validate(run.result)
        return ReviewOut(
            document=DocumentOut.of(document),
            run_id=run.id,
            model=run.model,
            prompt_version=run.prompt_version,
            result=run.result,
            invoice=None if invoice is None else invoice.model_dump(mode="json"),
            corrections=[
                CorrectionOut(field_path=c.field_path, old_value=c.old_value, new_value=c.new_value)
                for c in corrections
            ],
            fields=_fields(extracted, invoice, {c.field_path for c in corrections}),
            hs_lines=[
                HSLineOut(line_index=h.line_index, result=h.result, chosen_code=h.chosen_code)
                for h in repository.hs_lines(session, run.id)
            ],
        )

    @app.post("/documents/{document_id}/corrections")
    def correct(
        document_id: DocumentId, edits: list[CorrectionInput], session: SessionDep
    ) -> ReviewOut:
        _editable(_document(session, document_id))
        run = _run(session, document_id)
        corrections = repository.corrections_for(session, run.id)
        invoice = _reviewed_invoice(run, corrections)
        if invoice is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "no extracted invoice to correct")
        try:
            # Validate the whole batch first, so a bad edit stores none of them.
            apply_corrections(invoice, edits)
            for edit in edits:
                old = field_value(invoice, edit.field_path)
                repository.add_correction(session, run.id, edit.field_path, old, edit.new_value)
                invoice = apply_corrections(invoice, [edit])
        except InvalidCorrectionError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
        session.flush()
        return review(document_id, session)

    @app.put("/documents/{document_id}/lines/{line_index}/hs_code")
    def choose_hs_code(
        document_id: DocumentId,
        line_index: Annotated[int, Path(ge=0)],
        choice: HSChoice,
        session: SessionDep,
    ) -> HSLineOut:
        _editable(_document(session, document_id))
        run = _run(session, document_id)
        if not repository.tariff_code_exists(session, choice.code):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"{choice.code} is not a PCT code"
            )
        line = next(
            (h for h in repository.hs_lines(session, run.id) if h.line_index == line_index), None
        )
        if line is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no HS suggestions for that line")
        line.chosen_code = choice.code
        return HSLineOut(
            line_index=line.line_index, result=line.result, chosen_code=line.chosen_code
        )

    @app.post("/documents/{document_id}/hs/rerun", status_code=status.HTTP_202_ACCEPTED)
    def rerun_hs(
        document_id: DocumentId, session: SessionDep, settings: SettingsDep
    ) -> DocumentOut:
        """Queue an HS re-run for the lines whose HS step failed or was rate-limited."""
        _not_in_demo(settings, "HS re-run")
        document = _document(session, document_id)
        _editable(document)
        run = _run(session, document_id)
        lines = {
            record.line_index: HSLineResult.model_validate(record.result)
            for record in repository.hs_lines(session, run.id)
        }
        if not lines_needing_hs(lines):
            raise HTTPException(status.HTTP_409_CONFLICT, "every line's HS step finished")
        repository.set_status(session, document_id, "hs_queued")
        session.refresh(document)
        return DocumentOut.of(document)

    @app.post("/documents/{document_id}/approve")
    def approve(document_id: DocumentId, session: SessionDep) -> DocumentOut:
        document = _document(session, document_id)
        _editable(document)
        if _reviewed_invoice(_run(session, document_id), []) is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "no extracted invoice to approve")
        repository.approve(document)
        return DocumentOut.of(document)

    @app.post("/documents/{document_id}/retry", status_code=status.HTTP_202_ACCEPTED)
    def retry(document_id: DocumentId, session: SessionDep, settings: SettingsDep) -> DocumentOut:
        # The demo deletes an upload's file once processed, so there is nothing to retry.
        _not_in_demo(settings, "Retry")
        document = _document(session, document_id)
        if document.status != "failed":
            raise HTTPException(status.HTTP_409_CONFLICT, "only a failed document can be retried")
        repository.set_status(session, document_id, "queued")
        session.refresh(document)
        return DocumentOut.of(document)

    @app.get("/documents/{document_id}/export", response_model=None)
    def export(
        document_id: DocumentId,
        session: SessionDep,
        export_format: Annotated[Literal["json", "csv"], Query(alias="format")] = "json",
    ) -> JSONResponse | PlainTextResponse:
        document = _document(session, document_id)
        if document.status != "approved":
            raise HTTPException(status.HTTP_409_CONFLICT, "export needs an approved document")
        run = _run(session, document_id)
        corrections = repository.corrections_for(session, run.id)
        invoice = _reviewed_invoice(run, corrections)
        if invoice is None:  # approve refuses this, so only a changed database gets here
            raise HTTPException(status.HTTP_409_CONFLICT, "no extracted invoice")
        chosen = {h.line_index: h.chosen_code for h in repository.hs_lines(session, run.id)}
        if export_format == "csv":
            headers = {"Content-Disposition": f'attachment; filename="{document_id[:12]}.csv"'}
            return PlainTextResponse(
                export_csv(invoice, chosen), media_type="text/csv", headers=headers
            )
        provenance = {
            "document_id": document_id,
            "warnings": PipelineResult.model_validate(run.result).warnings,
            "run_id": run.id,
            "model": run.model,
            "prompt_version": run.prompt_version,
            "schema_version": run.schema_version,
            "corrections": len(corrections),
            "approved_at": None
            if document.approved_at is None
            else document.approved_at.isoformat(),
        }
        return JSONResponse(export_json(invoice, chosen, provenance))


def _uploader(visitor: str | None, settings: Settings) -> str | None:
    """A salted hash of the visitor's IP; None when the proxy didn't say who it was."""
    if not visitor or visitor == UNKNOWN_VISITOR:
        return None
    salt = settings.demo_uploader_salt.get_secret_value()
    return hashlib.sha256(f"{salt}:{visitor}".encode()).hexdigest()


def _check_demo_caps(session: Session, settings: Settings, uploader: str | None) -> None:
    repository.lock_upload_counts(session)
    since = datetime.now(UTC) - timedelta(days=1)  # a rolling day
    if repository.uploads_since(session, since) >= settings.demo_daily_uploads:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "The demo has reached today's upload limit. Try a sample invoice, or come back "
            "tomorrow.",
        )
    visitor_count = 0 if uploader is None else repository.uploads_since(session, since, uploader)
    if visitor_count >= settings.demo_visitor_daily_uploads:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"You've used your {settings.demo_visitor_daily_uploads} demo uploads for today.",
        )


def _not_in_demo(settings: Settings, action: str) -> None:
    if settings.demo_mode:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{action} is off in the public demo")


def _document(session: Session, document_id: str) -> Document:
    document = session.get(Document, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such document")
    return document


def _run(session: Session, document_id: str) -> ExtractionRun:
    run = repository.latest_run(session, document_id)
    if run is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "the document hasn't been processed yet")
    return run


def _editable(document: Document) -> None:
    if document.status not in REVIEWABLE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"a document that is {document.status} can't be reviewed"
        )


def _fields(
    extracted: PipelineResult, invoice: RawInvoice | None, corrected: set[str]
) -> list[FieldOut]:
    if invoice is None or extracted.invoice is None:
        return []
    return [
        FieldOut(
            path=assessment.path,
            value=field_value(invoice, assessment.path),
            extracted=field_value(extracted.invoice, assessment.path),
            status=assessment.status,
            confidence=assessment.confidence,
            reasons=assessment.reasons,
            corrected=assessment.path in corrected,
        )
        for assessment in extracted.assessments
    ]


def _reviewed_invoice(run: ExtractionRun, corrections: list[Any]) -> RawInvoice | None:
    extracted = PipelineResult.model_validate(run.result).invoice
    if extracted is None:
        return None
    edits = [CorrectionInput(field_path=c.field_path, new_value=c.new_value) for c in corrections]
    return apply_corrections(extracted, edits)
