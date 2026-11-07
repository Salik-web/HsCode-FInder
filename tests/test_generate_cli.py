"""End-to-end: real Chromium rendering and augmentation on a small dataset. No LLM involved."""

import hashlib
import random
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from synth import generate
from synth.augment import STRENGTH_RANGES, TRANSFORM_ORDER, augment_pdf
from synth.generate import MANIFEST_NAME, build_dataset
from synth.generator import GroundTruth, generate_ground_truth, load_catalogue
from synth.manifest import Manifest, ManifestEntry
from synth.render import (
    TEMPLATES,
    count_pages,
    find_missing_text,
    open_browser_page,
    render_html,
    render_pdf,
)

SEED = 42
N_CLEAN = 6
N_MESSY = 3


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("synthetic")
    build_dataset(N_CLEAN, N_MESSY, SEED, path)
    return path


@pytest.fixture(scope="module")
def manifest(out_dir: Path) -> Manifest:
    return Manifest.model_validate_json((out_dir / MANIFEST_NAME).read_bytes())


def clean_entries(manifest: Manifest) -> list[ManifestEntry]:
    return [entry for entry in manifest.documents if entry.dataset == "synthetic_clean"]


def messy_entries(manifest: Manifest) -> list[ManifestEntry]:
    return [entry for entry in manifest.documents if entry.dataset == "synthetic_messy"]


def folder_hashes(folder: Path) -> dict[str, str]:
    return {
        path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def test_document_counts(manifest: Manifest) -> None:
    assert len(clean_entries(manifest)) == N_CLEAN
    assert len(messy_entries(manifest)) == N_MESSY


def test_ids_are_unique(manifest: Manifest) -> None:
    ids = [entry.id for entry in manifest.documents]
    assert len(ids) == len(set(ids))


def test_folders_hold_exactly_what_the_manifest_lists(out_dir: Path, manifest: Manifest) -> None:
    listed = {entry.path for entry in manifest.documents}
    listed |= {entry.ground_truth for entry in manifest.documents}
    on_disk = set(folder_hashes(out_dir)) - {MANIFEST_NAME}
    assert on_disk == listed


def test_manifest_hashes_match_files(out_dir: Path, manifest: Manifest) -> None:
    for entry in manifest.documents:
        assert hashlib.sha256((out_dir / entry.path).read_bytes()).hexdigest() == entry.sha256


def test_ground_truth_files_are_schema_valid(out_dir: Path, manifest: Manifest) -> None:
    for entry in manifest.documents:
        GroundTruth.model_validate_json((out_dir / entry.ground_truth).read_bytes())


def test_messy_documents_share_source_split_and_truth(manifest: Manifest) -> None:
    clean_by_id = {entry.id: entry for entry in clean_entries(manifest)}
    for entry in messy_entries(manifest):
        source = clean_by_id[entry.source_id]
        assert entry.split == source.split
        assert entry.ground_truth == source.ground_truth


def test_messy_documents_record_their_transforms(manifest: Manifest) -> None:
    for entry in messy_entries(manifest):
        assert len(entry.transforms) >= 2


def test_messy_files_are_jpeg_images(out_dir: Path, manifest: Manifest) -> None:
    for entry in messy_entries(manifest):
        with Image.open(out_dir / entry.path) as image:
            assert image.format == "JPEG"


def test_rerun_gives_byte_identical_files(out_dir: Path, tmp_path: Path) -> None:
    # A new browser launch, seconds after the fixture's run, so surviving timestamps show up.
    build_dataset(N_CLEAN, N_MESSY, SEED, tmp_path)
    assert folder_hashes(tmp_path) == folder_hashes(out_dir)


def test_smaller_rerun_removes_stale_documents(tmp_path: Path) -> None:
    build_dataset(3, 1, SEED, tmp_path)
    build_dataset(1, 0, SEED, tmp_path)
    assert set(folder_hashes(tmp_path)) == {
        MANIFEST_NAME,
        "clean/inv_00000.pdf",
        "clean/inv_00000.json",
    }


@pytest.mark.parametrize("template", TEMPLATES)
def test_longest_invoice_fits_one_page_and_shows_its_values(template: str) -> None:
    catalogue = load_catalogue()
    candidates = (generate_ground_truth(SEED, index, catalogue) for index in range(2000))
    longest = next(
        truth
        for truth in candidates
        if len(truth.invoice.line_items) == 15 and truth.invoice.line_items[0].hs_code_on_doc
    )
    with open_browser_page() as page:
        pdf = render_pdf(page, render_html(longest.invoice, template))
    assert count_pages(pdf) == 1
    assert find_missing_text(pdf, longest.invoice, template) == []


def test_text_check_catches_a_wrong_total(out_dir: Path, manifest: Manifest) -> None:
    entry = clean_entries(manifest)[0]
    truth = GroundTruth.model_validate_json((out_dir / entry.ground_truth).read_bytes())
    tampered = truth.invoice.model_copy(update={"total_amount": truth.invoice.total_amount + 1})
    pdf = (out_dir / entry.path).read_bytes()
    assert find_missing_text(pdf, tampered, entry.template) == [str(tampered.total_amount)]


def test_pdf_metadata_holds_no_timestamps(out_dir: Path, manifest: Manifest) -> None:
    with pymupdf.open(out_dir / clean_entries(manifest)[0].path) as document:
        assert not document.metadata["creationDate"]
        assert not document.metadata["modDate"]


def test_augment_is_deterministic_for_a_seed(out_dir: Path, manifest: Manifest) -> None:
    pdf = (out_dir / clean_entries(manifest)[0].path).read_bytes()
    assert augment_pdf(pdf, random.Random(1)) == augment_pdf(pdf, random.Random(1))


def test_augment_strengths_stay_in_range(out_dir: Path, manifest: Manifest) -> None:
    pdf = (out_dir / clean_entries(manifest)[0].path).read_bytes()
    for seed in range(5):
        _, transforms = augment_pdf(pdf, random.Random(seed))
        assert [t.name for t in transforms] == [
            name for name in TRANSFORM_ORDER if name in {t.name for t in transforms}
        ]
        for transform in transforms:
            low, high = STRENGTH_RANGES[transform.name]
            assert low <= abs(transform.strength) <= high


@pytest.mark.parametrize(
    "argv",
    [
        ["--n", "0", "--messy", "0", "--seed", "1"],
        ["--n", "3", "--messy", "4", "--seed", "1"],
        ["--n", "3", "--messy", "-1", "--seed", "1"],
    ],
)
def test_cli_rejects_invalid_counts(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        generate.main(argv)


def test_cli_writes_to_the_configured_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    generate.main(["--n", "2", "--messy", "1", "--seed", "7"])
    assert (tmp_path / "synthetic" / MANIFEST_NAME).exists()
    assert "Generated 3 documents" in capsys.readouterr().out
