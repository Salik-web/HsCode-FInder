"""Injected-error set (synth/inject.py): exactly one labelled error per invoice."""

import random
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.raw import RawInvoice
from synth import inject
from synth.generator import ANCHOR_DATE, generate_ground_truth, load_catalogue
from synth.inject import (
    ERROR_KINDS,
    INJECTED_DIR,
    PlantingError,
    build_injected,
    load_injected,
    plant,
    typo,
)
from synth.manifest import MANIFEST_NAME

SEED = 2026
N = 2 * len(ERROR_KINDS)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("injected")
    build_injected(N, SEED, folder)
    return folder


def differing_paths(before: Any, after: Any, path: str = "") -> list[str]:
    """Every leaf where two JSON values differ, as pipeline paths."""
    if isinstance(before, dict):
        return [
            difference
            for key in before
            for difference in differing_paths(
                before[key], after[key], f"{path}.{key}" if path else key
            )
        ]
    if isinstance(before, list):
        return [
            difference
            for index, (b, a) in enumerate(zip(before, after, strict=True))
            for difference in differing_paths(b, a, f"{path}[{index}]")
        ]
    return [] if before == after else [path]


def test_every_kind_appears_equally_and_is_labelled(built: Path) -> None:
    manifest, documents = load_injected(built)
    assert manifest.as_of == ANCHOR_DATE
    assert [entry.kind for entry in manifest.documents] == list(ERROR_KINDS) * 2
    assert {entry.split for entry in manifest.documents} == {"injected"}
    for entry, document in zip(manifest.documents, documents, strict=True):
        assert document.error.kind == entry.kind
        assert entry.id.startswith("inj_")


def test_exactly_the_labelled_field_changes(built: Path) -> None:
    """Net and gross are swapped together; every other error touches one field only."""
    _, documents = load_injected(built)
    for document in documents:
        changed = differing_paths(document.source.model_dump(mode="json"), document.invoice)
        error = document.error
        if error.kind == "net_over_gross":
            assert sorted(changed) == ["gross_weight_kg", "net_weight_kg"]
        else:
            assert changed == [error.path], document.id
        assert error.original != error.planted


def test_only_the_planted_incoterm_breaks_the_schema(built: Path) -> None:
    _, documents = load_injected(built)
    for document in documents:
        if document.error.kind == "invalid_incoterm":
            with pytest.raises(ValidationError):
                RawInvoice.model_validate(document.invoice)
        else:
            RawInvoice.model_validate(document.invoice)


def test_planted_values_are_what_the_kind_promises(built: Path) -> None:
    manifest, documents = load_injected(built)
    for document in documents:
        invoice, error = document.invoice, document.error
        match error.kind:
            case "net_over_gross":
                assert Decimal(invoice["net_weight_kg"]) > Decimal(invoice["gross_weight_kg"])
            case "future_date":
                assert date.fromisoformat(invoice["invoice_date"]) > manifest.as_of
            case "bad_currency":
                assert invoice["currency"] in inject.NON_ISO_CURRENCIES
            case "invalid_incoterm":
                assert invoice["incoterm"] in inject.OUTDATED_INCOTERMS
            case "wrong_line_amount" | "wrong_total":
                assert error.method in inject.TYPO_METHODS


def test_rebuilding_gives_identical_files(built: Path, tmp_path: Path) -> None:
    build_injected(N, SEED, tmp_path)
    for path in built.iterdir():
        assert (tmp_path / path.name).read_bytes() == path.read_bytes(), path.name


def test_a_smaller_rebuild_leaves_no_stale_files(tmp_path: Path) -> None:
    build_injected(N, SEED, tmp_path)
    build_injected(len(ERROR_KINDS), SEED, tmp_path)
    names = sorted(path.name for path in tmp_path.iterdir())
    expected = [MANIFEST_NAME, *[f"inj_{i:05d}.json" for i in range(len(ERROR_KINDS))]]
    assert names == sorted(expected)


@pytest.mark.parametrize(
    "value", ["0.01", "0.10", "1.00", "11.11", "99.99", "100.00", "12129.84", "5.05"]
)
def test_typo_always_changes_the_value_and_keeps_it_positive(value: str) -> None:
    for seed in range(50):
        planted, method = typo(Decimal(value), random.Random(seed))
        assert planted != Decimal(value)
        assert planted > 0
        assert method in inject.TYPO_METHODS
        assert planted == planted.quantize(Decimal("0.01"))


def test_weights_that_cannot_be_swapped_into_an_error_are_refused() -> None:
    source = generate_ground_truth(SEED, 0, load_catalogue()).invoice
    equal = source.model_copy(update={"net_weight_kg": source.gross_weight_kg})
    with pytest.raises(PlantingError):
        plant("net_over_gross", equal, random.Random(0), ANCHOR_DATE)


def test_cli_refuses_the_main_dataset_seed_and_too_few_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from synth import generate

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    generate.main(["--n", "1", "--messy", "0", "--seed", "5"])
    with pytest.raises(generate.SharedSeedError):
        inject.main(["--n", "6", "--seed", "5"])
    with pytest.raises(SystemExit):
        inject.main(["--n", "5", "--seed", "6"])
    inject.main(["--n", "6", "--seed", "6"])
    assert (tmp_path / INJECTED_DIR / MANIFEST_NAME).exists()
