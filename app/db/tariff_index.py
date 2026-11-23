"""HS retrieval over tariff_codes: exact cosine search (pgvector) and full-text search."""

from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.db.models import TariffCode
from app.schemas.hs import HSCandidate
from app.schemas.tariff import EMBEDDING_TEXT_RECIPE

# Any word of the query may match (OR), ranked by cover density. The query's lexemes come
# from the same English parser as the column; each is quoted and passed through the
# 'simple' configuration, so it is used as is and can't be read as tsquery syntax.
FULL_TEXT_SEARCH = text(
    """
    WITH query AS (
        SELECT to_tsquery('simple', coalesce(string_agg(quote_literal(lexeme), ' | '), ''))
            AS terms
        FROM unnest(to_tsvector('english', :query))
    )
    SELECT code, path
    FROM tariff_codes, query
    WHERE search_vector @@ query.terms
    ORDER BY ts_rank_cd(search_vector, query.terms, 1) DESC, code
    LIMIT :limit
    """
)


class TariffIndexError(RuntimeError):
    """The tariff table can't serve this query: empty, or embedded another way."""


class PgVectorTariffIndex:
    def __init__(self, engine: Engine, embedding_model: str) -> None:
        self._engine = engine
        self._check_embeddings(embedding_model)

    def _check_embeddings(self, embedding_model: str) -> None:
        """Vectors from another model or text recipe aren't comparable: refuse, don't mis-rank."""
        statement = select(TariffCode.embedding_model, TariffCode.embedding_text).distinct()
        with Session(self._engine) as session:
            found = set(session.execute(statement).tuples())
        if not found:
            raise TariffIndexError("tariff_codes is empty: run `python -m tariff.ingest` first")
        expected = {(embedding_model, EMBEDDING_TEXT_RECIPE)}
        if found != expected:
            raise TariffIndexError(
                f"tariff_codes was embedded as {sorted(found)}, queries expect {expected}; "
                "re-run `python -m tariff.ingest`"
            )

    def search(self, query_vector: list[float], limit: int) -> list[HSCandidate]:
        distance = TariffCode.embedding.cosine_distance(query_vector)
        statement = (
            select(TariffCode.code, TariffCode.path, distance.label("distance"))
            .order_by(distance, TariffCode.code)
            .limit(limit)
        )
        with Session(self._engine) as session:
            rows = session.execute(statement).all()
        return [
            HSCandidate(code=row.code, path=row.path, rank=rank, similarity=1 - row.distance)
            for rank, row in enumerate(rows, start=1)
        ]

    def search_text(self, query: str, limit: int) -> list[HSCandidate]:
        with Session(self._engine) as session:
            rows = session.execute(FULL_TEXT_SEARCH, {"query": query, "limit": limit}).all()
        return [
            HSCandidate(code=row.code, path=row.path, rank=rank, similarity=None)
            for rank, row in enumerate(rows, start=1)
        ]
