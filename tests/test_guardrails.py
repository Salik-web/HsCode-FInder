"""Guardrail eval (evals/guardrails.py): what counts as caught, missed and a false alarm."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.schemas.raw import RawInvoice
from evals.guardrails import (
    CLEAN_DEV_NAME,
    build_guardrail_report,
    clean_summary,
    evaluate_injected,
    location_path,
    render_guardrail_markdown,
    write_guardrail_report,
)
from synth.generator import ANCHOR_DATE, generate_ground_truth, load_catalogue
from synth.inject import ERROR_KINDS, InjectedDocument, PlantedError, build_injected

AS_OF = ANCHOR_DATE


def source() -> RawInvoice:
    return generate_ground_truth(2026, 3, load_catalogue()).invoice


def injected(field: str, value: object, kind: str, path: str | None = None) -> InjectedDocument:
    clean = source()
    invoice = clean.model_dump(mode="json")
    invoice[field] = value
    error = PlantedError(
        kind=kind,  # type: ignore[arg-type]
        path=path or field,
        original=str(getattr(clean, field)),
        planted=str(value),
        method="test",
    )
    return InjectedDocument(id="inj_test", error=error, invoice=invoice, source=clean)


@pytest.mark.parametrize(
    ("location", "path"),
    [(("incoterm",), "incoterm"), (("line_items", 2, "amount"), "line_items[2].amount")],
)
def test_location_path_matches_check_paths(location: tuple, path: str) -> None:
    assert location_path(location) == path


def test_a_large_total_error_is_caught_by_a_check() -> None:
    total = source().total_amount
    assert total is not None
    outcome = evaluate_injected(injected("total_amount", str(total * 10), "wrong_total"), AS_OF)
    assert outcome.caught_by == "check"
    assert outcome.other_flags == []


def test_a_total_error_inside_rounding_is_missed() -> None:
    total = source().total_amount
    assert total is not None
    planted = str(total + Decimal("0.01"))  # a cent: what rounding alone can do
    outcome = evaluate_injected(injected("total_amount", planted, "wrong_total"), AS_OF)
    assert outcome.caught_by is None


def test_an_outdated_incoterm_is_caught_by_the_schema() -> None:
    outcome = evaluate_injected(injected("incoterm", "DDU", "invalid_incoterm"), AS_OF)
    assert outcome.caught_by == "schema"


@pytest.mark.parametrize(
    ("field", "printed", "kind"),
    [("incoterm", "C&F", "invalid_incoterm"), ("currency", "RMB", "bad_currency")],
)
def test_an_exact_synonym_is_mapped_as_in_the_pipeline_not_flagged(
    field: str, printed: str, kind: str
) -> None:
    outcome = evaluate_injected(injected(field, printed, kind), AS_OF)
    assert outcome.caught_by is None
    assert outcome.other_flags == []


def test_a_flag_on_another_field_only_is_not_a_catch() -> None:
    """A wrong line amount flagged only through the total is not caught on its own field."""
    clean = source()
    invoice = clean.model_dump(mode="json")
    invoice["total_amount"] = str((clean.total_amount or Decimal(0)) * 10)
    error = PlantedError(
        kind="wrong_line_amount",
        path="line_items[0].amount",
        original="x",
        planted="y",
        method="test",
    )
    document = InjectedDocument(id="inj_test", error=error, invoice=invoice, source=clean)
    outcome = evaluate_injected(document, AS_OF)
    assert outcome.caught_by is None
    assert outcome.other_flags == ["total_amount"]


def test_net_over_gross_is_caught_through_either_weight() -> None:
    clean = source()
    invoice = clean.model_dump(mode="json")
    invoice["net_weight_kg"], invoice["gross_weight_kg"] = (
        invoice["gross_weight_kg"],
        invoice["net_weight_kg"],
    )
    error = PlantedError(
        kind="net_over_gross", path="net_weight_kg", original="a", planted="b", method="swap"
    )
    document = InjectedDocument(id="inj_test", error=error, invoice=invoice, source=clean)
    outcome = evaluate_injected(document, AS_OF)
    assert outcome.caught_by == "check"
    assert outcome.other_flags == []  # gross is part of the same error, not collateral


def test_clean_ground_truth_raises_no_alarms() -> None:
    catalogue = load_catalogue()
    invoices = [generate_ground_truth(42, index, catalogue).invoice for index in range(50)]
    summary = clean_summary(CLEAN_DEV_NAME, invoices, AS_OF)
    assert (summary.documents, summary.flagged, summary.false_alarm_rate) == (50, 0, 0.0)


def test_a_flagged_clean_invoice_is_a_false_alarm() -> None:
    wrong_date = source().model_copy(update={"invoice_date": date(2030, 1, 1)})
    summary = clean_summary("x", [source(), wrong_date], AS_OF)
    assert summary.flagged == 1
    assert summary.alarms_by_rule == {"date_sanity": 1}
    assert summary.false_alarm_rate == 0.5


def test_report_counts_each_kind_and_renders(tmp_path: Path) -> None:
    folder = tmp_path / "injected"
    build_injected(2 * len(ERROR_KINDS), 2026, folder)
    from synth.inject import load_injected

    manifest, documents = load_injected(folder)
    clean_sets = {"sources": [document.source for document in documents]}
    report = build_guardrail_report(documents, manifest.seed, manifest.as_of, clean_sets)
    assert [kind.kind for kind in report.kinds] == list(ERROR_KINDS)
    assert all(kind.documents == 2 for kind in report.kinds)
    assert report.clean[0].flagged == 0
    markdown = render_guardrail_markdown(report)
    assert "## Catch rate on the injected set" in markdown
    assert "| sources | 12 | 0 | 0.0% | none |" in markdown
    path = write_guardrail_report(report, tmp_path / "reports")
    assert path.exists() and path.with_suffix(".json").exists()


def test_a_guardrail_report_beside_eval_reports_breaks_nothing(tmp_path: Path) -> None:
    """Regression: evals.run parsed every JSON in the reports folder as an eval report."""
    from evals.report import find_previous

    folder = tmp_path / "injected"
    build_injected(len(ERROR_KINDS), 2026, folder)
    from synth.inject import load_injected

    manifest, documents = load_injected(folder)
    report = build_guardrail_report(documents, manifest.seed, manifest.as_of, {})
    reports = tmp_path / "reports"
    write_guardrail_report(report, reports)
    from datetime import UTC, datetime

    from evals.report import RunMetadata

    meta = RunMetadata(
        run_id="now_synthetic_clean_dev_m_v3",
        created_at=datetime.now(UTC),
        git_sha=None,
        git_dirty=None,
        dataset="synthetic_clean",
        split="dev",
        model="m",
        prompt_version="v3",
        schema_version="1.0",
        documents_total=0,
        documents_scored=0,
        llm_calls_made=0,
        llm_calls_cached=0,
    )
    assert find_previous(reports, meta) is None


@pytest.mark.parametrize(
    ("field", "planted", "kind", "review"),
    [
        ("total_amount", "1.00", "wrong_total", "failed_check"),
        ("incoterm", "DDU", "invalid_incoterm", "document_needs_review"),
        ("currency", "EURO", "bad_currency", "failed_check"),
    ],
)
def test_a_planted_error_reaches_review(field: str, planted: str, kind: str, review: str) -> None:
    assert evaluate_injected(injected(field, planted, kind), AS_OF).review == review


def test_a_planted_error_that_passes_every_check_stays_ok() -> None:
    total = source().total_amount
    assert total is not None
    planted = str(total + Decimal("0.01"))  # inside rounding: no check, no text layer
    assert evaluate_injected(injected("total_amount", planted, "wrong_total"), AS_OF).review == "ok"


def test_clean_ground_truth_sends_no_field_to_review() -> None:
    invoices = [generate_ground_truth(42, index, load_catalogue()).invoice for index in range(20)]
    summary = clean_summary("x", invoices, AS_OF)
    assert summary.fields > 0
    assert summary.fields_sent_to_review == 0
