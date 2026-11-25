import hashlib
import json
from decimal import Decimal

from app.llm.client import (
    LLMError,
    LLMResponse,
    RateLimitedError,
    RequestTooLargeError,
    Usage,
)
from app.pipeline.run import PipelineConfig, run_pipeline
from app.schemas.constants import SCHEMA_VERSION
from synth.generator import ANCHOR_DATE, generate_ground_truth, load_catalogue
from tests.fakes import FakeClient
from tests.test_preprocess import image_pdf, text_pdf

CONFIG = PipelineConfig(prompt_version="v1", as_of=ANCHOR_DATE)


def answer_with_truth() -> LLMResponse:
    truth = generate_ground_truth(42, 5, load_catalogue()).invoice
    return LLMResponse(
        content=truth.model_dump_json(),
        model="model-a-001",
        usage=Usage(input_tokens=10, output_tokens=5),
        latency_ms=7,
        cost_usd=Decimal("0"),
    )


def test_successful_run_records_versions_and_stages() -> None:
    pdf = image_pdf(2)
    result = run_pipeline(pdf, CONFIG, FakeClient("model-a", [answer_with_truth()]))
    assert result.document_sha256 == hashlib.sha256(pdf).hexdigest()
    assert result.schema_version == SCHEMA_VERSION
    assert result.prompt_version == "v1"
    assert result.model == "model-a"
    assert result.page_count == 2
    assert result.preprocess.status == result.extract.status == "ok"
    assert result.invoice is not None
    assert result.llm_calls[0].model == "model-a-001"


def test_bad_file_fails_preprocess_and_never_calls_the_llm() -> None:
    client = FakeClient("model-a", [])
    result = run_pipeline(b"not a document", CONFIG, client)
    assert result.preprocess.status == "failed"
    assert result.preprocess.retryable is False
    assert result.extract.status == "not_run"
    assert result.invoice is None
    assert client.requests == []


def test_failed_extract_keeps_the_preprocess_result() -> None:
    client = FakeClient("model-a", [RateLimitedError("quota")])
    result = run_pipeline(image_pdf(1), CONFIG, client)
    assert result.preprocess.status == "ok"
    assert result.page_count == 1
    assert result.extract.status == "failed"
    assert result.extract.retryable is True


def test_result_never_contains_unvalidated_output() -> None:
    client = FakeClient("model-a", [answer_with_truth().model_copy(update={"content": "{}"})] * 2)
    result = run_pipeline(image_pdf(1), CONFIG, client)
    assert result.extract.status == "needs_review"
    assert result.invoice is None
    assert "{}" not in result.model_dump_json()


def test_rules_run_after_extraction_and_are_recorded() -> None:
    truth = generate_ground_truth(42, 5, load_catalogue()).invoice
    lines = [truth.line_items[0].model_copy(update={"unit": "MET"}), *truth.line_items[1:]]
    buyer = truth.buyer.model_copy(update={"name": "Same as consignee"})
    answer = answer_with_truth().model_copy(
        update={
            "content": truth.model_copy(
                update={"line_items": lines, "buyer": buyer}
            ).model_dump_json()
        }
    )
    result = run_pipeline(image_pdf(1), CONFIG, FakeClient("model-a", [answer]))
    assert result.invoice.buyer == truth.consignee
    assert any("resolved_by_rule" in change for change in result.rules_applied)
    assert [check.path for check in result.field_checks] == ["line_items[0].unit"]
    assert result.invoice.line_items[0].unit == "MET"  # kept, flagged, never guessed


def test_failed_extraction_runs_no_rules() -> None:
    result = run_pipeline(image_pdf(1), CONFIG, FakeClient("model-a", [RateLimitedError("q")]))
    assert result.rules_applied == []
    assert result.field_checks == []


def test_mapped_code_synonyms_are_recorded_before_the_rules() -> None:
    truth = generate_ground_truth(42, 5, load_catalogue()).invoice
    content = truth.model_dump(mode="json") | {"incoterm": "CNF"}
    response = answer_with_truth().model_copy(update={"content": json.dumps(content)})
    result = run_pipeline(image_pdf(1), CONFIG, FakeClient("model-a", [response]))
    assert result.invoice is not None
    assert result.invoice.incoterm == "CFR"
    assert result.rules_applied[0] == "incoterm: 'CNF' -> 'CFR'"


# --- documents longer than the model's image limit --------------------------------------------

LIMITED = CONFIG.model_copy(update={"max_images": 3})


def test_a_four_page_pdf_sends_three_pages_and_warns() -> None:
    client = FakeClient("model-a", [answer_with_truth()])
    pdf = text_pdf(["Invoice", "Lines", "Totals", "Terms and conditions"])
    result = run_pipeline(pdf, LIMITED, client)
    [request] = client.requests
    assert len(request.images) == 3
    assert "Totals" in request.text
    assert "Terms and conditions" not in request.text
    assert result.extract.status == "ok"
    assert (result.page_count, result.pages_read) == (4, 3)
    [warning] = result.warnings
    assert warning.startswith("Only the first 3 of 4 pages were read.")


def test_the_retry_after_a_rejected_answer_keeps_to_the_limit_too() -> None:
    rejected = answer_with_truth().model_copy(update={"content": "{}"})
    client = FakeClient("model-a", [rejected, answer_with_truth()])
    run_pipeline(image_pdf(4), LIMITED, client)
    assert [len(request.images) for request in client.requests] == [3, 3]


def test_a_document_at_the_limit_is_read_whole_without_a_warning() -> None:
    client = FakeClient("model-a", [answer_with_truth()])
    result = run_pipeline(image_pdf(3), LIMITED, client)
    assert len(client.requests[0].images) == 3
    assert (result.page_count, result.pages_read, result.warnings) == (3, 3, [])


def test_without_a_limit_every_page_is_sent() -> None:
    client = FakeClient("model-a", [answer_with_truth()])
    result = run_pipeline(image_pdf(4), CONFIG, client)
    assert len(client.requests[0].images) == 4
    assert result.warnings == []


def test_a_provider_error_carries_a_plain_message_next_to_the_technical_one() -> None:
    error = LLMError("groq HTTP 400: Too many images provided.")
    result = run_pipeline(image_pdf(4), LIMITED, FakeClient("model-a", [error]))
    assert result.extract.error == "LLMError: groq HTTP 400: Too many images provided."
    assert result.extract.message == LLMError.plain
    assert result.extract.retryable is False  # the pipeline itself wouldn't retry it


def test_pages_are_dropped_until_the_request_fits() -> None:
    """Regression: on Groq's free tier three pages pass the image limit but exceed the input
    tokens allowed in a minute (HTTP 413, "Request too large")."""
    client = FakeClient("model-a", [RequestTooLargeError("413"), answer_with_truth()])
    pdf = text_pdf(["Invoice", "Lines", "Totals", "Terms and conditions"])
    result = run_pipeline(pdf, LIMITED, client)
    assert [len(request.images) for request in client.requests] == [3, 2]
    assert "Lines" in client.requests[1].text
    assert "Totals" not in client.requests[1].text
    assert result.extract.status == "ok"
    assert (result.page_count, result.pages_read) == (4, 2)
    assert result.warnings[0].startswith("Only the first 2 of 4 pages were read.")


def test_one_page_that_is_too_large_fails_with_a_plain_message() -> None:
    client = FakeClient("model-a", [RequestTooLargeError("413")] * 3)
    result = run_pipeline(image_pdf(4), LIMITED, client)
    assert [len(request.images) for request in client.requests] == [3, 2, 1]
    assert result.extract.status == "failed"
    assert result.extract.message == RequestTooLargeError.plain


def test_a_rejected_first_answer_is_still_recorded_when_the_retry_is_too_large() -> None:
    rejected = answer_with_truth().model_copy(update={"content": "{}"})
    client = FakeClient("model-a", [rejected, RequestTooLargeError("413"), answer_with_truth()])
    result = run_pipeline(image_pdf(2), LIMITED, client)
    assert [len(request.images) for request in client.requests] == [2, 2, 1]
    assert len(result.llm_calls) == 2  # both answered calls, so cost and tokens are complete
    assert result.pages_read == 1
