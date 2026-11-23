"""Shared fixtures: a Postgres test database migrated from scratch, and an API client.

Tests that use them are skipped when no Postgres is reachable (set DATABASE_URL or
POSTGRES_* as for the app).
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.api.main import create_app
from app.config import MissingDatabaseConfigError, Settings
from app.db.models import TariffCode
from app.db.session import make_engine
from app.schemas.tariff import EMBEDDING_TEXT_RECIPE

TEST_DATABASE = "tradedoc_test"
TARIFF = ("6109.1000", "6105.1000")
WORKFLOW_TABLES = "corrections, hs_suggestions, extraction_runs, documents, upload_log"


@pytest.fixture(scope="session")
def database_url() -> str:
    try:
        base = Settings().sqlalchemy_url()
    except MissingDatabaseConfigError:
        pytest.skip("no database configured")
    admin = create_engine(base, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": TEST_DATABASE}
            )
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{TEST_DATABASE}"'))
    except OperationalError:
        pytest.skip("Postgres isn't reachable")
    finally:
        admin.dispose()
    url = make_url(base).set(database=TEST_DATABASE).render_as_string(hide_password=False)
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url  # alembic's env.py reads Settings()
    try:
        command.upgrade(Config("alembic.ini"), "head")
    finally:
        if previous is None:
            del os.environ["DATABASE_URL"]
        else:
            os.environ["DATABASE_URL"] = previous
    return url


@pytest.fixture
def settings(database_url: str, tmp_path: Path) -> Settings:
    return Settings(_env_file=None, database_url=SecretStr(database_url), storage_dir=tmp_path)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    engine = make_engine(settings)
    with Session(engine) as session, session.begin():
        session.execute(text(f"TRUNCATE {WORKFLOW_TABLES}, tariff_codes"))
        for code in TARIFF:
            session.add(
                TariffCode(
                    code=code,
                    description="x",
                    path=f"Garments > {code}",
                    chapter=code[:2],
                    heading=code[:4],
                    tariff_version="test",
                    embedding_model="fake",
                    embedding_text=EMBEDDING_TEXT_RECIPE,
                    embedding=[0.0] * 384,
                )
            )
    engine.dispose()
    with TestClient(create_app(settings)) as test_client:
        yield test_client
