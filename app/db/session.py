"""Database engine from settings. Callers own their sessions; nothing global here."""

from sqlalchemy import Engine, create_engine

from app.config import Settings

CONNECT_TIMEOUT_SECONDS = 10


def make_engine(settings: Settings) -> Engine:
    # pre_ping drops connections a hosted database (Neon) closed while idle.
    return create_engine(
        settings.sqlalchemy_url(),
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )
