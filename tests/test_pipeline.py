import hashlib
from decimal import Decimal

from app.llm.client import LLMResponse, RateLimitedError, Usage
from app.pipeline.run import PipelineConfig, run_pipeline
from app.schemas.constants import SCHEMA_VERSION
from synth.generator import generate_ground_truth, load_catalogue
from tests.fakes import FakeClient
from tests.test_preprocess import image_pdf

CONFIG = PipelineConfig(prompt_version="v1")


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
