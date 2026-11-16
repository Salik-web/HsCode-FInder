"""The whole harness end to end, with a fake LLM that answers from the ground truth.

If an oracle doesn't score exactly 100%, or one planted error doesn't move exactly one
field, the harness is wrong and every reported number is suspect.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.llm.cache import CachingClient
from app.llm.client import (
    LLMError,
    LLMRequest,
    LLMResponse,
    RateLimitedError,
    RequestTooLargeError,
    Usage,
)
from app.schemas.raw import RawInvoice
from app.schemas.result import LLMCall
from evals.dataset import DatasetFileMismatchError, EvalDocument, load_documents
from evals.report import DocumentRecord, EvalReport, operational_stats
from evals.run import RunConfig, run_eval
from synth.generate import SYNTHETIC_DIR, build_dataset, build_diagnostic, diagnostic_dir
from synth.generator import ANCHOR_DATE
from synth.manifest import Dataset, Split

SEED = 42
N_CLEAN = 16
N_MESSY = 3


class OracleClient:
    """Answers each document with its ground truth, except where told otherwise."""

    def __init__(
        self,
        truths: dict[str, RawInvoice],
        overrides: dict[str, list[str | Exception]] | None = None,
    ) -> None:
        self._truths = truths
        self._overrides = {sha: list(answers) for sha, answers in (overrides or {}).items()}
        self.requests: list[LLMRequest] = []

    @property
    def model(self) -> str:
        return "oracle"

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        queued = self._overrides.get(request.document_sha256)
        content: str | Exception = self._truths[request.document_sha256].model_dump_json()
        if queued:
            content = queued.pop(0)
        if isinstance(content, Exception):
            raise content
        return LLMResponse(
            content=content,
            model="oracle-1",
            usage=Usage(input_tokens=100, output_tokens=50),
            latency_ms=1000,
            cost_usd=Decimal("0.0001"),
        )


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("data")
    build_dataset(N_CLEAN, N_MESSY, SEED, root / SYNTHETIC_DIR)
    return root


def documents(data_dir: Path, dataset: Dataset = "synthetic_clean", split: Split = "dev"):
    return load_documents(data_dir, dataset, split)


def truths(docs: list[EvalDocument]) -> dict[str, RawInvoice]:
    return {doc.entry.sha256: doc.truth.invoice for doc in docs}


def evaluate(
    docs: list[EvalDocument], client: object, reports_dir: Path, split: Split = "dev"
) -> tuple[EvalReport, Path]:
    config = RunConfig(
        prompt_version="v1",
        dataset=docs[0].entry.dataset,
        split=split,
        model="oracle",
        reasoning_effort=None,
        as_of=ANCHOR_DATE,
    )
    return run_eval(docs, client, config, reports_dir, concurrency=2)  # type: ignore[arg-type]


def all_fields(report: EvalReport, group: str = "seen") -> dict[str, float | None]:
    return {f.field: f.accuracy for f in report.layout_groups[group].summary.fields}


def test_oracle_scores_exactly_one_hundred_percent(data_dir: Path, tmp_path: Path) -> None:
    docs = documents(data_dir)
    report, _ = evaluate(docs, OracleClient(truths(docs)), tmp_path)
    seen = report.layout_groups["seen"].summary
    assert report.meta.complete
    assert seen.documents == len(docs)
    assert set(all_fields(report).values()) == {1.0}
    assert seen.critical_field_accuracy == seen.all_critical_correct_rate == 1.0
    assert seen.line_precision == seen.line_recall == seen.line_f1 == 1.0


def test_oracle_is_perfect_on_messy_images_too(data_dir: Path, tmp_path: Path) -> None:
    docs = documents(data_dir, "synthetic_messy")
    report, _ = evaluate(docs, OracleClient(truths(docs)), tmp_path)
    assert set(all_fields(report).values()) == {1.0}


def test_one_planted_error_moves_exactly_one_field(data_dir: Path, tmp_path: Path) -> None:
    docs = documents(data_dir)
    target = docs[0]
    wrong = target.truth.invoice.model_copy(update={"currency": "XXX"})
    client = OracleClient(truths(docs), {target.entry.sha256: [wrong.model_dump_json()]})
    report, _ = evaluate(docs, client, tmp_path)
    accuracy = all_fields(report)
    assert accuracy.pop("currency") == pytest.approx((len(docs) - 1) / len(docs))
    assert set(accuracy.values()) == {1.0}
    seen = report.layout_groups["seen"].summary
    assert seen.all_critical_correct_rate == pytest.approx((len(docs) - 1) / len(docs))


def test_a_dropped_line_moves_recall_and_nothing_else(data_dir: Path, tmp_path: Path) -> None:
    docs = documents(data_dir)
    target = next(doc for doc in docs if len(doc.truth.invoice.line_items) > 1)
    dropped = target.truth.invoice.model_copy(
        update={"line_items": target.truth.invoice.line_items[1:]}
    )
    client = OracleClient(truths(docs), {target.entry.sha256: [dropped.model_dump_json()]})
    report, _ = evaluate(docs, client, tmp_path)
    seen = report.layout_groups["seen"].summary
    gold_lines = sum(len(doc.truth.invoice.line_items) for doc in docs)
    assert seen.line_precision == 1.0
    assert seen.line_recall == pytest.approx((gold_lines - 1) / gold_lines)
    assert set(all_fields(report).values()) == {1.0}


def test_output_invalid_twice_scores_as_no_output(data_dir: Path, tmp_path: Path) -> None:
    docs = documents(data_dir)
    target = docs[1]
    client = OracleClient(truths(docs), {target.entry.sha256: ["not json", "{}"]})
    report, _ = evaluate(docs, client, tmp_path)
    record = next(r for r in report.documents if r.id == target.entry.id)
    assert record.status == "scored"
    assert record.extract_status == "needs_review"
    assert {field.outcome for field in record.score.fields} == {"no_output"}
    operations = report.layout_groups["seen"].operations
    assert operations.needs_review_rate == pytest.approx(1 / len(docs))
    assert operations.retry_rate == pytest.approx(1 / len(docs))
    assert operations.first_try_answered == len(docs)
    assert operations.first_try_valid == len(docs) - 1


def test_first_try_rate_counts_documents_fixed_by_the_retry(data_dir: Path, tmp_path: Path) -> None:
    docs = documents(data_dir)
    fixed = {docs[0].entry.sha256: ["not json", docs[0].truth.invoice.model_dump_json()]}
    too_large = {docs[1].entry.sha256: [RequestTooLargeError("too large")]}
    report, path = evaluate(docs, OracleClient(truths(docs), fixed | too_large), tmp_path)
    operations = report.layout_groups["seen"].operations
    # The too-large document never got an answer, so it isn't in the denominator.
    assert operations.first_try_answered == len(docs) - 1
    assert operations.first_try_valid == len(docs) - 2
    assert operations.retried == 1
    markdown = path.read_text(encoding="utf-8")
    first_try = f"{(len(docs) - 2) / (len(docs) - 1) * 100:.1f}% ({len(docs) - 2}/{len(docs) - 1})"
    assert f"| First-try valid JSON | {first_try} | — (0/0) |" in markdown
    assert f"| Retry rate | {100 / len(docs):.1f}% (1/{len(docs)}) |" in markdown


def test_non_retryable_llm_error_scores_as_no_output(data_dir: Path, tmp_path: Path) -> None:
    docs = documents(data_dir)
    client = OracleClient(truths(docs), {docs[0].entry.sha256: [LLMError("HTTP 400")]})
    report, _ = evaluate(docs, client, tmp_path)
    assert report.meta.complete
    assert report.layout_groups["seen"].operations.failed_rate == pytest.approx(1 / len(docs))


def test_request_too_large_fails_that_document_and_the_run_continues(
    data_dir: Path, tmp_path: Path
) -> None:
    docs = documents(data_dir)
    too_large = RequestTooLargeError("Request too large: output tokens per minute")
    client = OracleClient(truths(docs), {docs[0].entry.sha256: [too_large]})
    report, path = evaluate(docs, client, tmp_path)
    assert report.meta.complete
    assert report.meta.documents_scored == len(docs)
    record = next(r for r in report.documents if r.id == docs[0].entry.id)
    assert record.extract_status == "failed"
    assert {field.outcome for field in record.score.fields} == {"no_output"}
    assert "RequestTooLargeError" in path.read_text(encoding="utf-8")


def test_quota_stops_the_run_and_a_rerun_finishes_from_cache(
    data_dir: Path, tmp_path: Path
) -> None:
    docs = documents(data_dir)
    cache_dir = tmp_path / "cache"
    reports_dir = tmp_path / "reports"
    limited = OracleClient(
        truths(docs), {d.entry.sha256: [RateLimitedError("quota")] for d in docs[3:]}
    )
    config = RunConfig(
        prompt_version="v1",
        dataset="synthetic_clean",
        split="dev",
        model="oracle",
        reasoning_effort=None,
        as_of=ANCHOR_DATE,
    )
    first, first_path = run_eval(
        docs, CachingClient(limited, cache_dir, {}), config, reports_dir, 1
    )
    assert not first.meta.complete
    assert first.meta.documents_scored == 3
    assert "INCOMPLETE" in first_path.read_text(encoding="utf-8")
    # Stopped after the first quota error instead of trying every remaining document.
    assert len(limited.requests) == 4
    not_scored = [r for r in first.documents if r.status == "not_scored"]
    assert len(not_scored) == len(docs) - 3
    assert all(r.score is None for r in not_scored)
    # Scored documents count once; unscored ones are never counted right or wrong.
    assert first.layout_groups["seen"].summary.documents == 3

    healthy = OracleClient(truths(docs))
    second, second_path = run_eval(
        docs, CachingClient(healthy, cache_dir, {}), config, reports_dir, 1
    )
    assert second.meta.complete
    assert len(healthy.requests) == len(docs) - 3  # the first three came from cache
    assert second.meta.llm_calls_cached == 3
    assert set(all_fields(second).values()) == {1.0}
    assert first.meta.run_id in second_path.read_text(encoding="utf-8")  # diffed against it

    third, _ = run_eval(
        docs, CachingClient(OracleClient({}), cache_dir, {}), config, reports_dir, 1
    )
    assert third.meta.llm_calls_made == 0
    assert third.meta.llm_calls_cached == len(docs)


def test_test_split_reports_totals_only(data_dir: Path, tmp_path: Path) -> None:
    docs = documents(data_dir, split="test")
    report, path = evaluate(docs, OracleClient(truths(docs)), tmp_path, split="test")
    assert report.documents is None
    markdown = path.read_text(encoding="utf-8")
    json_text = path.with_suffix(".json").read_text(encoding="utf-8")
    for doc in docs:
        assert doc.entry.id not in markdown
        assert doc.entry.id not in json_text
    assert "can't be used for tuning" in markdown


def test_unseen_split_reports_the_unseen_group_only(data_dir: Path, tmp_path: Path) -> None:
    docs = documents(data_dir, split="test_unseen_layout")
    report, _ = evaluate(docs, OracleClient(truths(docs)), tmp_path, split="test_unseen_layout")
    assert report.layout_groups["seen"].summary.documents == 0
    assert report.layout_groups["unseen"].summary.documents == len(docs)
    assert report.documents is None
    assert set(report.templates) <= {"sidebar", "ledger"}


def test_report_never_blends_seen_and_unseen(data_dir: Path, tmp_path: Path) -> None:
    docs = documents(data_dir)
    _, path = evaluate(docs, OracleClient(truths(docs)), tmp_path)
    markdown = path.read_text(encoding="utf-8")
    assert "| Metric | Seen | Unseen |" in markdown
    assert "Overall" not in markdown and "Total" not in markdown.split("## By template")[0]


def test_reference_buyers_are_their_own_slice(data_dir: Path, tmp_path: Path) -> None:
    docs = documents(data_dir)
    references = [doc for doc in docs if doc.buyer_form == "reference"]
    assert references, "the small dataset should include a reference buyer"
    # The model copies the consignee correctly everywhere except reference buyers.
    overrides = {}
    for doc in references:
        blank_buyer = doc.truth.invoice.buyer.model_copy(update={"name": None, "address": None})
        answer = doc.truth.invoice.model_copy(update={"buyer": blank_buyer})
        overrides[doc.entry.sha256] = [answer.model_dump_json()]
    report, path = evaluate(docs, OracleClient(truths(docs), overrides), tmp_path)
    reference = {f.field: f for f in report.buyer_forms["reference"]["seen"]}
    assert reference["buyer.name"].accuracy == 0.0
    assert reference["buyer.name"].missed == len(references)
    assert reference["buyer.country"].accuracy == 1.0
    for form in ("repeated", "distinct"):
        assert {f.accuracy for f in report.buyer_forms[form]["seen"]} <= {1.0}
    assert "| reference | `buyer.name` | 0.0%" in path.read_text(encoding="utf-8")


def test_dev_report_lists_the_worst_failures(data_dir: Path, tmp_path: Path) -> None:
    docs = documents(data_dir)
    wrong = docs[2].truth.invoice.model_copy(update={"invoice_number": "WRONG-1"})
    client = OracleClient(truths(docs), {docs[2].entry.sha256: [wrong.model_dump_json()]})
    _, path = evaluate(docs, client, tmp_path)
    markdown = path.read_text(encoding="utf-8")
    assert "1 worst failures (of 1 documents with any error)" in markdown
    assert f"### {docs[2].entry.id}" in markdown
    assert "WRONG-1" in markdown


def test_report_json_round_trips(data_dir: Path, tmp_path: Path) -> None:
    docs = documents(data_dir)
    report, path = evaluate(docs, OracleClient(truths(docs)), tmp_path)
    loaded = EvalReport.model_validate_json(path.with_suffix(".json").read_bytes())
    assert loaded == report
    assert json.loads(path.with_suffix(".json").read_text())["meta"]["prompt_version"] == "v1"


def test_edited_file_is_refused(data_dir: Path, tmp_path: Path) -> None:
    doc = documents(data_dir)[0]
    copy = tmp_path / "copy.pdf"
    copy.write_bytes(doc.path.read_bytes() + b"\n")
    with pytest.raises(DatasetFileMismatchError):
        doc.model_copy(update={"path": copy}).read_verified_bytes()


def test_empty_group_shows_no_cost_rather_than_unknown(data_dir: Path, tmp_path: Path) -> None:
    """Regression: a dev report showed the empty unseen column's cost as "unknown"."""
    docs = documents(data_dir)
    _, path = evaluate(docs, OracleClient(truths(docs)), tmp_path)
    assert "| Cost per document | $0.00010 | — |" in path.read_text(encoding="utf-8")


def test_reports_from_before_the_first_try_counts_still_load() -> None:
    """Regression: the diff step loads every earlier report, including the Gemini ones."""
    reports = sorted(Path("evals/reports").glob("*gemini*.json"))
    assert reports
    for path in reports:
        report = EvalReport.model_validate_json(path.read_bytes())
        assert report.layout_groups["seen"].operations.first_try_answered is None


def test_repaired_and_ignored_keys_are_counted(data_dir: Path, tmp_path: Path) -> None:
    docs = documents(data_dir)
    data = docs[0].truth.invoice.model_dump(mode="json")
    data[" currency"] = data.pop("currency")
    data["document_type"] = "Commercial Invoice"
    client = OracleClient(truths(docs), {docs[0].entry.sha256: [json.dumps(data)]})
    report, path = evaluate(docs, client, tmp_path)
    operations = report.layout_groups["seen"].operations
    assert operations.keys_repaired == 1
    assert operations.keys_ignored == 1
    assert operations.first_try_valid == len(docs)
    assert set(all_fields(report).values()) == {1.0}
    markdown = path.read_text(encoding="utf-8")
    assert "| Keys repaired | 1 | 0 |" in markdown
    assert "| Keys ignored | 1 | 0 |" in markdown


def test_offline_run_scores_cached_documents_and_never_stops(
    data_dir: Path, tmp_path: Path
) -> None:
    from app.llm.offline import OfflineClient

    docs = documents(data_dir)
    cache_dir = tmp_path / "cache"
    # Cache answers for every document except the first.
    for doc in docs[1:]:
        warm = CachingClient(OracleClient(truths(docs)), cache_dir, {})
        config = RunConfig(
            prompt_version="v1",
            dataset="synthetic_clean",
            split="dev",
            model="oracle",
            reasoning_effort=None,
            as_of=ANCHOR_DATE,
        )
        run_eval([doc], warm, config, tmp_path / "warm_reports", 1)
    offline_config = RunConfig(
        prompt_version="v1",
        dataset="synthetic_clean",
        split="dev",
        model="oracle",
        reasoning_effort=None,
        as_of=ANCHOR_DATE,
        offline=True,
    )
    client = CachingClient(OfflineClient("oracle"), cache_dir, {})
    report, _ = run_eval(docs, client, offline_config, tmp_path / "reports", 1)
    assert report.meta.documents_scored == len(docs) - 1  # the first wasn't cached
    assert report.meta.llm_calls_made == 0
    not_scored = [r for r in report.documents if r.status == "not_scored"]
    assert [r.id for r in not_scored] == [docs[0].entry.id]
    assert "NotCachedError" in not_scored[0].error


def test_rule_counts_appear_in_the_report(data_dir: Path, tmp_path: Path) -> None:
    docs = documents(data_dir)
    target = docs[0].truth.invoice
    lines = [target.line_items[0].model_copy(update={"unit": "MET"}), *target.line_items[1:]]
    data = target.model_copy(update={"line_items": lines}).model_dump(mode="json")
    data["buyer"]["name"] = "Same as consignee"
    data["total_amount"] = f"{target.total_amount:,.2f}"
    client = OracleClient(truths(docs), {docs[0].entry.sha256: [json.dumps(data)]})
    report, path = evaluate(docs, client, tmp_path)
    operations = report.layout_groups["seen"].operations
    assert operations.references_resolved == 1
    assert operations.unit_checks_failed == 1
    markdown = path.read_text(encoding="utf-8")
    assert "| Buyer references resolved by rule | 1 | 0 |" in markdown
    assert "| Unit checks failed | 1 | 0 |" in markdown
    assert "Check failed: line_items[0].unit" in markdown


def test_diagnostic_batch_loads_from_its_folder_with_details(
    data_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path / "data"
    folder = diagnostic_dir(root, "ledger")
    build_diagnostic("ledger", 2, SEED + 1, folder)
    docs = load_documents(root, "synthetic_clean", "diagnostic", folder=folder.name)
    assert [doc.entry.id for doc in docs] == ["diag_00000", "diag_00001"]
    report, _ = evaluate(docs, OracleClient(truths(docs)), tmp_path / "reports", "diagnostic")
    assert report.documents is not None
    assert len(report.documents) == 2
    assert report.layout_groups["unseen"].summary.documents == 2
    # The main dataset never holds a diagnostic document.
    assert load_documents(data_dir, "synthetic_clean", "diagnostic") == []


def timed_record(*calls: tuple[int, int | None, int | None]) -> DocumentRecord:
    """A scored-looking record whose calls are (latency_ms, call_ms, throttle_ms)."""
    return DocumentRecord(
        id="inv",
        template="classic",
        layout_group="seen",
        buyer_form="distinct",
        status="scored",
        preprocess_status="ok",
        extract_status="ok",
        error=None,
        score=None,
        llm_calls=[
            LLMCall(
                attempt=attempt,
                model="m",
                input_tokens=None,
                output_tokens=None,
                latency_ms=latency,
                call_ms=call,
                throttle_ms=throttle,
                cost_usd=None,
                cached=True,
                validation_errors=[],
            )
            for attempt, (latency, call, throttle) in enumerate(calls, start=1)
        ],
    )


def test_model_call_time_is_split_from_backoff_and_throttle_wait() -> None:
    records = [
        # One retry: 1.5 s + 2 s of model time, 30 s of SDK backoff, 60 s throttled.
        timed_record((31_500, 1_500, 60_000), (2_000, 2_000, 0)),
        timed_record((1_000, 1_000, 0)),
    ]
    stats = operational_stats(records)
    assert stats.timed_documents == stats.throttled_documents == 2
    assert stats.model_call_p95_ms == 3_500
    assert stats.model_call_p50_ms == 1_000
    assert stats.backoff_p95_ms == 30_000
    assert stats.throttle_p95_ms == 60_000
    assert stats.latency_p95_ms == 33_500  # call + backoff, as before


def test_calls_cached_before_the_split_are_left_out_of_model_call_time() -> None:
    """A document counts only when every call recorded the split; the rest show n."""
    old = timed_record((40_000, None, None))
    mixed = timed_record((9_000, None, None), (2_000, 2_000, 0))
    new = timed_record((1_200, 1_000, 500))
    stats = operational_stats([old, mixed, new])
    assert stats.timed_documents == 1
    assert stats.model_call_p95_ms == 1_000
    assert stats.backoff_p95_ms == 200
    assert stats.latency_p95_ms == 40_000
    none_timed = operational_stats([old])
    assert none_timed.timed_documents == 0
    assert none_timed.model_call_p95_ms is None


def test_report_shows_model_call_time_with_its_count(data_dir: Path, tmp_path: Path) -> None:
    docs = documents(data_dir)
    _, path = evaluate(docs, OracleClient(truths(docs)), tmp_path)
    text = path.read_text(encoding="utf-8")
    # The oracle predates the split, like answers cached before 2026-11-16.
    assert "| Model call time p95 (target < 30 s) | not recorded | — |" in text
    assert "| Call + backoff p95 (all documents) | 1.0 s | — |" in text


def test_oracle_answers_raise_no_guardrail_alarms(data_dir: Path, tmp_path: Path) -> None:
    docs = documents(data_dir)
    report, path = evaluate(docs, OracleClient(truths(docs)), tmp_path)
    ops = report.layout_groups["seen"].operations
    assert (ops.alarms_on_wrong_values, ops.alarms_on_correct_values) == (0, 0)
    assert ops.correct_documents == ops.documents == len(docs)
    assert ops.correct_documents_flagged == 0
    assert report.meta.as_of == ANCHOR_DATE
    text = path.read_text(encoding="utf-8")
    assert f"| Fully correct documents flagged (false alarms) | 0.0% (0/{len(docs)}) | — |" in text


def test_a_check_on_a_wrong_value_is_a_true_alarm(data_dir: Path, tmp_path: Path) -> None:
    """Regression too: check notes used to sit inside the failure table and break it."""
    docs = documents(data_dir)
    target = docs[0]
    wrong = target.truth.invoice.model_copy(update={"currency": "XXX"})
    client = OracleClient(truths(docs), {target.entry.sha256: [wrong.model_dump_json()]})
    report, path = evaluate(docs, client, tmp_path)
    ops = report.layout_groups["seen"].operations
    assert (ops.alarms_on_wrong_values, ops.alarms_on_correct_values) == (1, 0)
    assert report.documents is not None
    record = next(r for r in report.documents if r.id == target.entry.id)
    assert record.alarms is not None
    assert [(a.path, a.rule, a.value_correct) for a in record.alarms] == [
        ("currency", "iso_currency", False)
    ]
    text = path.read_text(encoding="utf-8")
    section = text.split(f"### {target.entry.id} ")[1].split("\n### ")[0]
    assert "- Check failed: currency: 'XXX' is not an ISO 4217 currency (value is wrong)" in section
    table = section.split("| --- | --- | --- | --- |\n")[1].strip().splitlines()
    assert table and all(row.startswith("| ") for row in table)


def test_reports_from_before_the_guardrails_still_load() -> None:
    for path in sorted(Path("evals/reports").glob("*.json")):
        report = EvalReport.model_validate_json(path.read_bytes())
        assert report.layout_groups["seen"].operations.alarms_on_wrong_values is None
