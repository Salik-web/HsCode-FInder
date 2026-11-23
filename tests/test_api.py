"""API + worker end to end on a real Postgres test database; every LLM call is mocked.

Skipped when no Postgres is reachable (set DATABASE_URL or POSTGRES_* as for the app).
"""

import dataclasses
import json
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
from app.llm.client import LLMResponse, RateLimitedError
from app.pipeline.prompts import QUERY_TASK, RERANK_TASK, load_task_prompt
from app.pipeline.validate import HSCodeTable
from app.schemas.hs import HSCandidate
from app.schemas.tariff import EMBEDDING_TEXT_RECIPE
from app.storage import DocumentStorage
from app.worker import WorkerDeps, process_next
from synth.generator import ANCHOR_DATE, generate_ground_truth, load_catalogue
from tests.fakes import FakeClient
from tests.test_pipeline import answer_with_truth
from tests.test_preprocess import image_pdf

TEST_DATABASE = "tradedoc_test"
TARIFF = ("6109.1000", "6105.1000")
WORKFLOW_TABLES = "corrections, hs_suggestions, extraction_runs, documents"


@pytest.fixture(scope="module")
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


class FakeEmbedder:
    def embed_query(self, _text: str) -> list[float]:
        return [1.0]


class FakeIndex:
    def search(self, _vector: list[float], _limit: int) -> list[HSCandidate]:
        return [
            HSCandidate(code=code, path=f"Garments > {code}", rank=rank, similarity=0.8)
            for rank, code in enumerate(TARIFF, start=1)
        ]

    def search_text(self, _query: str, _limit: int) -> list[HSCandidate]:
        return []


def hs_answers(lines: int) -> list[LLMResponse | Exception]:
    """A query answer and a rerank answer for each line, in the order the worker asks."""
    base = answer_with_truth()
    query = json.dumps({"tariff_description": "Garments of cotton", "headings": ["6109"]})
    rerank = json.dumps({"suggestions": [{"code": "6109.1000", "reason": "cotton garment"}]})
    return [base.model_copy(update={"content": c}) for _ in range(lines) for c in (query, rerank)]


def worker(settings: Settings, extraction: list[LLMResponse | Exception], lines: int) -> WorkerDeps:
    return WorkerDeps(
        engine=make_engine(settings),
        storage=DocumentStorage(settings.storage_dir),
        extract_client=FakeClient("model-a", extraction),
        hs_client=FakeClient("hs-model", hs_answers(lines)),
        embedder=FakeEmbedder(),
        index=FakeIndex(),
        hs_codes=HSCodeTable.from_codes(TARIFF),
        # v1 answers in the raw format that answer_with_truth() returns.
        invoice_prompt_version="v1",
        query_prompt=load_task_prompt(QUERY_TASK, "v1"),
        rerank_prompt=load_task_prompt(RERANK_TASK, "v1"),
    )


def upload(client: TestClient, data: bytes, name: str = "invoice.pdf") -> dict:
    response = client.post("/documents", files={"file": (name, data, "application/pdf")})
    assert response.status_code == 202, response.text
    return response.json()


def line_count() -> int:
    return len(generate_ground_truth(42, 5, load_catalogue()).invoice.line_items)


def test_upload_is_idempotent_and_queues_once(client: TestClient) -> None:
    pdf = image_pdf(1)
    first = upload(client, pdf, "../../secret name.pdf")
    assert first["status"] == "queued"
    assert first["filename"] == "secret_name.pdf"
    second = upload(client, pdf, "other.pdf")
    assert second["id"] == first["id"]
    assert second["filename"] == "secret_name.pdf"
    page = client.get("/documents", params={"limit": 1}).json()
    assert page["total"] == 1
    assert len(page["items"]) == 1


def test_upload_rejects_other_file_types_and_oversized_files(client: TestClient) -> None:
    text_file = client.post("/documents", files={"file": ("a.txt", b"hello", "text/plain")})
    assert text_file.status_code == 415
    huge = b"%PDF-" + b"0" * (10 * 1024 * 1024)
    assert client.post("/documents", files={"file": ("a.pdf", huge)}).status_code == 413


def test_unknown_and_malformed_ids(client: TestClient) -> None:
    assert client.get("/documents/" + "0" * 64).status_code == 404
    assert client.get("/documents/not-a-sha").status_code == 422


def test_upload_review_correct_approve_export(client: TestClient, settings: Settings) -> None:
    document = upload(client, image_pdf(1))
    assert client.get(f"/documents/{document['id']}/review").status_code == 409

    deps = worker(settings, [answer_with_truth()], line_count())
    assert process_next(deps, ANCHOR_DATE) == document["id"]
    assert process_next(deps, ANCHOR_DATE) is None

    processed = client.get(f"/documents/{document['id']}").json()
    assert processed["status"] == "needs_review"  # a photo: nothing can verify the fields
    assert processed["page_count"] == 1
    page = client.get(f"/documents/{document['id']}/pages/1")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("image/")

    review = client.get(f"/documents/{document['id']}/review").json()
    assert review["invoice"]["invoice_number"]
    assert {f["status"] for f in review["fields"]} >= {"needs_review"}
    assert len(review["hs_lines"]) == line_count()
    suggestions = review["hs_lines"][0]["result"]["hs"]["suggestions"]
    assert [s["code"] for s in suggestions] == ["6109.1000"]

    bad = client.post(
        f"/documents/{document['id']}/corrections",
        json=[
            {"field_path": "invoice_number", "new_value": "INV-9"},
            {"field_path": "total_amount", "new_value": "lots"},
        ],
    )
    assert bad.status_code == 422
    assert client.get(f"/documents/{document['id']}/review").json()["corrections"] == []

    old_number = review["invoice"]["invoice_number"]
    good = client.post(
        f"/documents/{document['id']}/corrections",
        json=[{"field_path": "invoice_number", "new_value": "INV-9"}],
    )
    assert good.status_code == 200, good.text
    corrected = good.json()
    assert corrected["invoice"]["invoice_number"] == "INV-9"
    assert corrected["result"]["invoice"]["invoice_number"] == old_number  # model output kept
    [number_field] = [f for f in corrected["fields"] if f["path"] == "invoice_number"]
    assert (number_field["value"], number_field["extracted"]) == ("INV-9", old_number)
    assert number_field["corrected"] is True
    assert corrected["corrections"] == [
        {"field_path": "invoice_number", "old_value": old_number, "new_value": "INV-9"}
    ]

    hs_url = f"/documents/{document['id']}/lines/0/hs_code"
    assert client.put(hs_url, json={"code": "9999.9999"}).status_code == 422
    assert client.put(hs_url, json={"code": "6109"}).status_code == 422
    assert client.put(hs_url, json={"code": "6105.1000"}).json()["chosen_code"] == "6105.1000"

    assert client.get(f"/documents/{document['id']}/export").status_code == 409
    assert client.post(f"/documents/{document['id']}/approve").json()["status"] == "approved"
    late = client.post(
        f"/documents/{document['id']}/corrections",
        json=[{"field_path": "invoice_number", "new_value": "INV-10"}],
    )
    assert late.status_code == 409

    exported = client.get(f"/documents/{document['id']}/export").json()
    assert exported["invoice"]["invoice_number"] == "INV-9"
    assert exported["invoice"]["line_items"][0]["hs_code_chosen"] == "6105.1000"
    assert exported["provenance"]["corrections"] == 1
    assert exported["provenance"]["prompt_version"] == "v1"
    as_csv = client.get(f"/documents/{document['id']}/export", params={"format": "csv"})
    assert as_csv.headers["content-type"].startswith("text/csv")
    assert as_csv.text.splitlines()[1].startswith("INV-9,")


def test_a_rate_limited_run_fails_retryably_and_can_be_requeued(
    client: TestClient, settings: Settings
) -> None:
    document = upload(client, image_pdf(1))
    deps = worker(settings, [RateLimitedError("quota")], 0)
    process_next(deps, ANCHOR_DATE)
    failed = client.get(f"/documents/{document['id']}").json()
    assert failed["status"] == "failed"
    assert failed["retryable"] is True
    assert "RateLimitedError" in failed["error"]
    assert client.post(f"/documents/{document['id']}/approve").status_code == 409
    assert client.post(f"/documents/{document['id']}/retry").json()["status"] == "queued"
    assert client.post(f"/documents/{document['id']}/retry").status_code == 409


def test_hs_rerun_refreshes_only_the_unfinished_lines(
    client: TestClient, settings: Settings
) -> None:
    lines = line_count()
    document = upload(client, image_pdf(1))
    deps = worker(settings, [answer_with_truth()], lines)
    limited = [RateLimitedError("quota")] * lines  # every line's first (query) call
    process_next(dataclasses.replace(deps, hs_client=FakeClient("hs-model", limited)), ANCHOR_DATE)
    url = f"/documents/{document['id']}"
    review = client.get(f"{url}/review").json()
    assert {h["result"]["hs"]["stage"]["status"] for h in review["hs_lines"]} == {"failed"}

    edited = client.post(
        f"{url}/corrections",
        json=[{"field_path": "line_items[0].description", "new_value": "Polo shirts, cotton"}],
    )
    assert edited.status_code == 200
    assert client.post(f"{url}/hs/rerun").json()["status"] == "hs_queued"
    late = client.post(
        f"{url}/corrections", json=[{"field_path": "invoice_number", "new_value": "X"}]
    )
    assert late.status_code == 409  # no edits while the worker re-runs HS

    retried = dataclasses.replace(deps, hs_client=FakeClient("hs-model", hs_answers(lines)))
    assert process_next(retried, ANCHOR_DATE) == document["id"]
    assert retried.hs_client.requests[0].text.count("Polo shirts, cotton") == 1  # corrected text
    after = client.get(f"{url}/review").json()
    assert after["document"]["status"] == "needs_review"  # the photo's fields still need a person
    assert {h["result"]["hs"]["stage"]["status"] for h in after["hs_lines"]} == {"ok"}
    assert after["invoice"]["line_items"][0]["description"] == "Polo shirts, cotton"
    assert len(after["corrections"]) == 1  # the review survived the re-run
    assert client.post(f"{url}/hs/rerun").status_code == 409  # nothing left to re-run
