"""API + worker end to end on a real Postgres test database; every LLM call is mocked."""

import dataclasses
import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.db.session import make_engine
from app.llm.client import LLMError, LLMResponse, RateLimitedError
from app.pipeline.prompts import QUERY_TASK, RERANK_TASK, load_task_prompt
from app.pipeline.validate import HSCodeTable
from app.schemas.hs import HSCandidate
from app.storage import DocumentStorage
from app.worker import WorkerDeps, process_next
from synth.generator import ANCHOR_DATE, generate_ground_truth, load_catalogue
from tests.conftest import TARIFF
from tests.fakes import FakeClient
from tests.test_pipeline import answer_with_truth
from tests.test_preprocess import image_pdf


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
        max_images=3,
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
    assert failed["error"] == RateLimitedError.plain
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


def test_a_four_page_pdf_is_read_up_to_the_limit_and_says_so(
    client: TestClient, settings: Settings
) -> None:
    document = upload(client, image_pdf(4))
    deps = worker(settings, [answer_with_truth()], line_count())
    process_next(deps, ANCHOR_DATE)
    assert len(deps.extract_client.requests[0].images) == 3
    url = f"/documents/{document['id']}"
    processed = client.get(url).json()
    assert processed["status"] == "needs_review"
    assert processed["page_count"] == 4
    review = client.get(f"{url}/review").json()
    assert review["result"]["pages_read"] == 3
    [warning] = review["result"]["warnings"]
    assert warning.startswith("Only the first 3 of 4 pages were read.")
    assert client.get(f"{url}/pages/4").status_code == 200  # the reviewer sees every page
    client.post(f"{url}/approve")
    assert client.get(f"{url}/export").json()["provenance"]["warnings"] == [warning]


def test_a_provider_error_is_plain_and_the_document_can_be_retried(
    client: TestClient, settings: Settings
) -> None:
    document = upload(client, image_pdf(4))
    raw = "groq HTTP 400: Error code: 400 - Too many images provided."
    process_next(worker(settings, [LLMError(raw)], 0), ANCHOR_DATE)
    url = f"/documents/{document['id']}"
    failed = client.get(url).json()
    assert failed["status"] == "failed"
    assert failed["error"] == LLMError.plain
    assert failed["retryable"] is True
    # The technical detail stays with the run, for whoever debugs it.
    assert raw in client.get(f"{url}/review").json()["result"]["extract"]["error"]
    assert client.post(f"{url}/retry").json()["status"] == "queued"
    process_next(worker(settings, [answer_with_truth()], line_count()), ANCHOR_DATE)
    retried = client.get(url).json()
    assert (retried["status"], retried["error"]) == ("needs_review", None)


def test_a_bad_file_stays_not_retryable(client: TestClient, settings: Settings) -> None:
    document = upload(client, b"%PDF-1.7 this is not really a pdf")
    process_next(worker(settings, [], 0), ANCHOR_DATE)
    failed = client.get(f"/documents/{document['id']}").json()
    assert (failed["status"], failed["retryable"]) == ("failed", False)
