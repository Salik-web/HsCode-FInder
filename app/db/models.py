"""SQLAlchemy models: the tariff, and documents with their runs, HS suggestions and corrections."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# bge-small-en-v1.5 writes 384-dimensional vectors (Settings.embedding_model).
EMBEDDING_DIMENSIONS = 384


class Base(DeclarativeBase):
    pass


class TariffCode(Base):
    """One 8-digit PCT code with the embedding of its path, for HS retrieval.

    No vector index: an exact scan of ~7,600 rows takes milliseconds and returns exactly
    what an in-memory search would. Add HNSW if the table grows by orders of magnitude.
    Full-text search over the path uses the GIN index on search_vector.
    """

    __tablename__ = "tariff_codes"

    code: Mapped[str] = mapped_column(String(9), primary_key=True)
    description: Mapped[str] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    chapter: Mapped[str] = mapped_column(String(2))
    heading: Mapped[str] = mapped_column(String(4))
    tariff_version: Mapped[str] = mapped_column(String(16))
    # The query side must use the same model and text recipe; the index checks both.
    embedding_model: Mapped[str] = mapped_column(String(128))
    embedding_text: Mapped[str] = mapped_column(String(32))
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    # Postgres keeps this in step with path; GIN-indexed for the lexical half of retrieval.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', path)", persisted=True)
    )

    __table_args__ = (
        Index("ix_tariff_codes_search_vector", "search_vector", postgresql_using="gin"),
    )


class Document(Base):
    """An uploaded file, addressed by the sha256 of its bytes (design rule 7)."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))  # sanitised, display only
    media_type: Mapped[str] = mapped_column(String(32))
    size_bytes: Mapped[int] = mapped_column(Integer)
    # queued -> processing -> needs_review | ready | failed; needs_review | ready -> approved
    status: Mapped[str] = mapped_column(String(16), index=True)
    error: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExtractionRun(Base):
    """One pipeline run on a document: the full PipelineResult plus what reproduces it."""

    __tablename__ = "extraction_runs"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(16))
    schema_version: Mapped[str] = mapped_column(String(16))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HSSuggestionRecord(Base):
    """HS suggestions for one line of a run, and the code a person chose."""

    __tablename__ = "hs_suggestions"
    __table_args__ = (UniqueConstraint("run_id", "line_index"),)

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("extraction_runs.id"), index=True)
    line_index: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(128))
    query_prompt_version: Mapped[str] = mapped_column(String(16))
    rerank_prompt_version: Mapped[str] = mapped_column(String(16))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)  # HSLineResult
    chosen_code: Mapped[str | None] = mapped_column(String(9))


class Correction(Base):
    """A reviewer's edit to one extracted field; replayed in order onto the run's invoice."""

    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("extraction_runs.id"), index=True)
    field_path: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[Any] = mapped_column(JSONB)
    new_value: Mapped[Any] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
