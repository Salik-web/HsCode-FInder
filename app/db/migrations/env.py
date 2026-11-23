"""Alembic environment: migrations run online against the configured database."""

from logging.config import fileConfig

from alembic import context

from app.config import Settings
from app.db.models import Base
from app.db.session import make_engine

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)


def run_migrations() -> None:
    engine = make_engine(Settings())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
