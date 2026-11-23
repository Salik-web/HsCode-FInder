"""Public demo: upload caps, visitor hashing, deletion, retention, sample seeding."""

import dataclasses
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api.main import create_app
from app.config import Settings
from app.db import repository
from app.db.models import Document
from app.db.session import make_engine
from app.demo import SeedError, seed_samples
from app.llm.client import RateLimitedError
from app.retention import cleanup
from app.storage import DocumentStorage
from app.worker import process_next
from synth.generator import ANCHOR_DATE
from tests.test_api import line_count, worker
from tests.test_pipeline import answer_with_truth
from tests.test_preprocess import image_pdf

DEMO = {
    "demo_mode": True,
    "demo_visitor_daily_uploads": 2,
    "demo_daily_uploads": 3,
    "demo_hs_lines": 1,
}


@pytest.fixture
def demo_settings(settings: Settings) -> Settings:
    return settings.model_copy(update=DEMO)


@pytest.fixture
def demo_client(client: TestClient, demo_settings: Settings) -> TestClient:
    """The shared client's database (truncated, tariff loaded), with demo settings."""
    del client  # requested only for its database reset
    return TestClient(create_app(demo_settings))


def post(client: TestClient, data: bytes, visitor: str | None = None) -> object:
    headers = {} if visitor is None else {"X-Visitor": visitor}
    return client.post("/documents", files={"file": ("a.pdf", data)}, headers=headers)


def test_visitor_and_global_daily_caps(demo_client: TestClient) -> None:
    pdfs = [image_pdf(pages) for pages in (1, 2, 3, 4)]
    assert post(demo_client, pdfs[0], "203.0.113.7").status_code == 202
    assert post(demo_client, pdfs[0], "203.0.113.7").status_code == 202  # same bytes: free
    assert post(demo_client, pdfs[1], "203.0.113.7").status_code == 202
    third = post(demo_client, pdfs[2], "203.0.113.7")
    assert third.status_code == 429
    assert "your 2 demo uploads" in third.json()["detail"]
    assert post(demo_client, pdfs[2], "198.51.100.9").status_code == 202
    over = post(demo_client, pdfs[3], "198.51.100.10")
    assert over.status_code == 429
    assert "today's upload limit" in over.json()["detail"]


def test_an_unknown_visitor_is_limited_only_by_the_global_cap(demo_client: TestClient) -> None:
    for pages in (1, 2, 3):
        assert post(demo_client, image_pdf(pages), "unknown").status_code == 202
    assert post(demo_client, image_pdf(4)).status_code == 429


def test_only_a_salted_hash_of_the_ip_is_stored(
    demo_client: TestClient, demo_settings: Settings
) -> None:
    first = post(demo_client, image_pdf(1), "203.0.113.7").json()["id"]
    second = post(demo_client, image_pdf(2), "198.51.100.9").json()["id"]
    engine = make_engine(demo_settings)
    with Session(engine) as session:
        stored = [session.get(Document, document_id).uploader for document_id in (first, second)]
    engine.dispose()
    assert all(len(value) == 64 and "203.0.113" not in value for value in stored)
    assert stored[0] != stored[1]  # two visitors hash differently


def test_retry_and_hs_rerun_are_off_in_the_demo(demo_client: TestClient) -> None:
    document = post(demo_client, image_pdf(1), "203.0.113.7").json()
    for action in ("retry", "hs/rerun"):
        response = demo_client.post(f"/documents/{document['id']}/{action}")
        assert response.status_code == 409
        assert "off in the public demo" in response.json()["detail"]


def test_demo_worker_caps_hs_lines_and_deletes_the_upload(
    demo_client: TestClient, demo_settings: Settings
) -> None:
    document = post(demo_client, image_pdf(1), "203.0.113.7").json()
    deps = dataclasses.replace(
        worker(demo_settings, [answer_with_truth()], 1),
        hs_line_limit=1,
        delete_original_after=True,
    )
    assert process_next(deps, ANCHOR_DATE) == document["id"]
    review = demo_client.get(f"/documents/{document['id']}/review").json()
    assert len(review["hs_lines"]) == 1 < line_count()
    assert not deps.storage.has_original(document["id"])
    assert deps.storage.has_pages(document["id"])  # the review still shows the page


def test_a_failed_upload_is_deleted_too(demo_client: TestClient, demo_settings: Settings) -> None:
    document = post(demo_client, image_pdf(1), "203.0.113.7").json()
    deps = dataclasses.replace(
        worker(demo_settings, [RateLimitedError("quota")], 0), delete_original_after=True
    )
    process_next(deps, ANCHOR_DATE)
    assert not deps.storage.has_original(document["id"])


# --- retention ---------------------------------------------------------------------------


def make_sample(settings: Settings, samples_dir: Path) -> str:
    samples_dir.mkdir(exist_ok=True)
    (samples_dir / "sample.pdf").write_bytes(image_pdf(1))
    deps = worker(settings, [answer_with_truth()], line_count())
    [sample_id] = seed_samples(deps, samples_dir, ANCHOR_DATE)
    return sample_id


def test_cleanup_purges_expired_and_orphaned_uploads_and_resets_samples(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    sample_id = make_sample(settings, tmp_path / "samples")
    edited = client.post(
        f"/documents/{sample_id}/corrections",
        json=[{"field_path": "invoice_number", "new_value": "VISITOR-EDIT"}],
    )
    assert edited.status_code == 200
    expired = post(client, image_pdf(2)).json()["id"]
    orphan = post(client, image_pdf(3)).json()["id"]
    fresh = post(client, image_pdf(4)).json()["id"]
    storage = DocumentStorage(settings.storage_dir)
    storage.delete_all(orphan)  # a restarted Space lost its files
    engine = make_engine(settings)
    with Session(engine) as session, session.begin():
        session.execute(
            update(Document)
            .where(Document.id.in_([expired, sample_id]))
            .values(
                created_at=datetime.now(UTC) - timedelta(hours=2),
                updated_at=datetime.now(UTC) - timedelta(hours=2),
            )
        )

    report = cleanup(engine, storage, 60, datetime.now(UTC))
    assert (report.purged, report.orphans, report.samples_reset) == (1, 1, 1)
    assert client.get(f"/documents/{expired}").status_code == 404
    assert client.get(f"/documents/{orphan}").status_code == 404
    assert client.get(f"/documents/{fresh}").status_code == 200
    assert not storage.has_original(expired)
    sample = client.get(f"/documents/{sample_id}/review").json()
    assert sample["corrections"] == []
    assert sample["invoice"]["invoice_number"] != "VISITOR-EDIT"
    assert sample["document"]["status"] in ("needs_review", "ready")
    engine.dispose()


# --- seeding -----------------------------------------------------------------------------


def test_seeding_is_idempotent_and_survives_a_lost_disk(
    client: TestClient, settings: Settings, tmp_path: Path
) -> None:
    samples_dir = tmp_path / "samples"
    sample_id = make_sample(settings, samples_dir)
    listed = client.get("/documents").json()["items"]
    assert listed[0]["id"] == sample_id  # samples are listed first

    # Already seeded: no LLM call at all (an empty fake client fails on any call).
    idle = worker(settings, [], 0)
    assert seed_samples(idle, samples_dir, ANCHOR_DATE) == [sample_id]

    # A restarted Space lost the page images: the sample is processed again.
    DocumentStorage(settings.storage_dir).delete_all(sample_id)
    seed_samples(worker(settings, [answer_with_truth()], line_count()), samples_dir, ANCHOR_DATE)
    assert DocumentStorage(settings.storage_dir).has_pages(sample_id)
    with Session(make_engine(settings)) as session:
        assert repository.latest_run(session, sample_id) is not None


def test_seeding_from_an_empty_cache_fails_loudly(settings: Settings, tmp_path: Path) -> None:
    samples_dir = tmp_path / "samples"
    samples_dir.mkdir()
    (samples_dir / "sample.pdf").write_bytes(image_pdf(1))
    not_cached = worker(settings, [RateLimitedError("not cached")], 0)
    with pytest.raises(SeedError, match=r"sample\.pdf"):
        seed_samples(not_cached, samples_dir, ANCHOR_DATE)
