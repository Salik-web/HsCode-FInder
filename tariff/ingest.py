"""Embed the parsed tariff and load it into tariff_codes (pgvector). Idempotent.

uv run python -m tariff.ingest --csv tariff/pct_2026-27.csv --version 2026-27

Replaces the whole table in one transaction, so a search never sees half a tariff.
Run `alembic upgrade head` first.
"""

import argparse
import time
from pathlib import Path

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import TariffCode
from app.db.session import make_engine
from app.embeddings import SentenceTransformerEmbedder
from app.schemas.tariff import EMBEDDING_TEXT_RECIPE, TariffRow, embedding_text
from tariff.parse import read_csv


def tariff_records(
    rows: list[TariffRow], vectors: list[list[float]], version: str, embedding_model: str
) -> list[dict[str, object]]:
    return [
        {
            "code": row.code,
            "description": row.description,
            "path": row.path,
            "chapter": row.chapter,
            "heading": row.heading,
            "tariff_version": version,
            "embedding_model": embedding_model,
            "embedding_text": EMBEDDING_TEXT_RECIPE,
            "embedding": vector,
        }
        for row, vector in zip(rows, vectors, strict=True)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--version", required=True, help="tariff year, e.g. 2026-27")
    args = parser.parse_args()
    settings = Settings()
    rows = read_csv(args.csv)
    started = time.perf_counter()
    embedder = SentenceTransformerEmbedder(settings.embedding_model)
    vectors = embedder.embed_documents([embedding_text(row.path) for row in rows])
    embedded_s = time.perf_counter() - started
    records = tariff_records(rows, vectors, args.version, settings.embedding_model)
    with Session(make_engine(settings)) as session, session.begin():
        session.execute(delete(TariffCode))
        session.execute(insert(TariffCode), records)
    print(f"Loaded {len(records)} codes ({args.version}); embedding took {embedded_s:.0f} s")


if __name__ == "__main__":
    main()
