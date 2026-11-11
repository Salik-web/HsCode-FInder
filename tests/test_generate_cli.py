"""End-to-end: real Chromium rendering and augmentation on a small dataset. No LLM involved."""

import hashlib
import random
import re
import time
from pathlib import Path

import pypdfium2
import pytest
from PIL import Image

from app.schemas.raw import RawInvoice
from synth import generate
from synth.augment import STRENGTH_RANGES, TRANSFORM_ORDER, augment_pdf
from synth.generate import MANIFEST_NAME, build_dataset
from synth.generator import GroundTruth, generate_ground_truth, load_catalogue
from synth.layouts import (
    LABEL_SYNONYMS,
    MULTI_PAGE_TEMPLATES,
    TEMPLATES,
    UNSEEN_TEMPLATES,
    Labels,
    pick_labels,
)
from synth.manifest import UNSEEN_SPLIT, Manifest, ManifestEntry
from synth.render import (
    FIXED_PDF_TIMESTAMP,
    PDFIUM_WRAPPED_HYPHEN,
    count_pages,
    find_missing_text,
    normalise_pdf,
    open_browser_page,
    print_pdf,
    render_html,
    render_pdf,
)

SEED = 42
N_CLEAN = len(TEMPLATES)  # one document per layout
N_MESSY = 3
TIMESTAMP_RESOLUTION_SECONDS = 1.1
# Long enough to wrap inside the digit run even in the widest layout (`ledger`, full width).
DIGIT_HYPHEN_ADDRESS = (
    f"Gewerbepark {'-'.join(['12', '34', '56', '78', '90'] * 8)} 58635 Lobenstein"
)
LABELS = pick_labels(random.Random(SEED))
# The widest wording, which is the hardest for a narrow column or a single-page layout.
LONGEST_LABELS = Labels(
    **{field: max(options, key=len) for field, options in LABEL_SYNONYMS.items()}
)


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


def test_messy_sources_are_single_page(manifest: Manifest) -> None:
    clean_by_id = {entry.id: entry for entry in clean_entries(manifest)}
    for entry in messy_entries(manifest):
        assert clean_by_id[entry.source_id].pages == 1


def test_every_layout_appears_in_the_dataset(manifest: Manifest) -> None:
    assert {entry.template for entry in clean_entries(manifest)} == set(TEMPLATES)


def test_manifest_marks_held_out_layouts_as_unseen(manifest: Manifest) -> None:
    assert manifest.unseen_templates == list(UNSEEN_TEMPLATES)
    for entry in manifest.documents:
        is_unseen = entry.template in UNSEEN_TEMPLATES
        assert (entry.split == UNSEEN_SPLIT) == is_unseen
        assert (entry.layout_group == "unseen") == is_unseen


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


def pick_varied_invoices() -> dict[str, RawInvoice]:
    """Pick the invoices most likely to break a layout, plus a deliberate worst case."""
    catalogue = load_catalogue()
    invoices = [generate_ground_truth(SEED, index, catalogue).invoice for index in range(300)]
    with_hs = [invoice for invoice in invoices if invoice.line_items[0].hs_code_on_doc]
    without_hs = [invoice for invoice in invoices if not invoice.line_items[0].hs_code_on_doc]
    longest_with_hs = max(with_hs, key=lambda invoice: len(invoice.line_items))
    parties = [
        party
        for invoice in invoices
        for party in (invoice.shipper, invoice.buyer, invoice.consignee, invoice.notify_party)
        if party is not None
    ]
    wordiest = max(parties, key=lambda party: len(party.name) + len(party.address))
    # Regression: Cambria left stray characters in the text layer of this name, which PDFium
    # read back as "İçİ taş İnİ şaat" even though the page looked right.
    dotted_i = wordiest.model_copy(update={"name": "İçtaş İnşaat", "country": "TR"})
    return {
        "one_line": min(invoices, key=lambda invoice: len(invoice.line_items)),
        "longest_with_hs": longest_with_hs,
        "longest_without_hs": max(without_hs, key=lambda invoice: len(invoice.line_items)),
        "largest_total": max(invoices, key=lambda invoice: invoice.total_amount),
        "smallest_total": min(invoices, key=lambda invoice: invoice.total_amount),
        "non_ascii_party": next(i for i in invoices if not i.shipper.name.isascii()),
        "ampersand_party": next(i for i in invoices if "&" in i.shipper.name),
        "dotted_capital_i": invoices[87].model_copy(update={"shipper": dotted_i}),
        "separate_buyer_and_notify": next(
            i for i in invoices if i.buyer != i.consignee and i.notify_party is not None
        ),
        "buyer_is_consignee_no_notify": next(
            i for i in invoices if i.buyer == i.consignee and i.notify_party is None
        ),
        # Every party block at its longest, on the longest invoice: the most crowded page.
        "worst_case": longest_with_hs.model_copy(
            update={
                "shipper": wordiest,
                "buyer": wordiest,
                "consignee": wordiest,
                "notify_party": wordiest,
            }
        ),
    }


@pytest.mark.parametrize("template", TEMPLATES)
def test_every_layout_handles_every_kind_of_invoice(template: str) -> None:
    max_pages = 2 if template in MULTI_PAGE_TEMPLATES else 1
    with open_browser_page() as page:
        for name, invoice in pick_varied_invoices().items():
            pdf = render_pdf(page, render_html(invoice, template, LONGEST_LABELS))
            assert count_pages(pdf) <= max_pages, name
            assert find_missing_text(pdf, invoice, template) == [], name


@pytest.mark.parametrize("template", TEMPLATES)
def test_text_check_expects_the_reference_instead_of_the_buyer(template: str) -> None:
    invoice = pick_varied_invoices()["buyer_is_consignee_no_notify"]
    with open_browser_page() as page:
        pdf = render_pdf(page, render_html(invoice, template, LABELS, "Same as consignee"))
    assert find_missing_text(pdf, invoice, template, "Same as consignee") == []
    assert find_missing_text(pdf, invoice, template, "As per consignee") == ["As per consignee"]


@pytest.mark.parametrize("template", MULTI_PAGE_TEMPLATES)
def test_long_invoices_span_two_pages_and_short_ones_do_not(template: str) -> None:
    varied = pick_varied_invoices()
    with open_browser_page() as page:
        long_pdf = render_pdf(page, render_html(varied["longest_with_hs"], template, LABELS))
        short_pdf = render_pdf(page, render_html(varied["one_line"], template, LABELS))
    assert count_pages(long_pdf) == 2
    assert count_pages(short_pdf) == 1
    assert find_missing_text(long_pdf, varied["longest_with_hs"], template) == []


def test_text_check_handles_a_hyphen_at_a_line_wrap() -> None:
    """Regression: invoice 91 failed in `compact`, where this address wraps at its hyphen."""
    invoice = generate_ground_truth(SEED, 0, load_catalogue()).invoice
    shipper = invoice.shipper.model_copy(
        update={"address": "H.No. 098 Prasad Ganj, Anantapuram-986086", "country": "IN"}
    )
    wrapped = invoice.model_copy(update={"shipper": shipper})
    with open_browser_page() as page:
        pdf = render_pdf(page, render_html(wrapped, "compact", LABELS))
    with pypdfium2.PdfDocument(pdf) as document:
        text = document[0].get_textpage().get_text_bounded()
    assert PDFIUM_WRAPPED_HYPHEN in text, "address no longer wraps, so this test proves nothing"
    assert find_missing_text(pdf, wrapped, "compact") == []


@pytest.mark.parametrize("template", TEMPLATES)
def test_text_check_handles_a_hyphen_kept_before_a_line_break(template: str) -> None:
    """Regression: after a digit, PDFium keeps a wrapped hyphen and the line break (`4-` + break).

    A long run of digit-hyphen pairs makes the wrap land on one, whatever the column width.
    """
    invoice = generate_ground_truth(SEED, 0, load_catalogue()).invoice
    shipper = invoice.shipper.model_copy(update={"address": DIGIT_HYPHEN_ADDRESS, "country": "DE"})
    wrapped = invoice.model_copy(update={"shipper": shipper})
    with open_browser_page() as page:
        pdf = render_pdf(page, render_html(wrapped, template, LABELS))
    with pypdfium2.PdfDocument(pdf) as document:
        text = " ".join(page.get_textpage().get_text_bounded() for page in document)
    assert re.search(r"\d-\r\n\d", text), "no wrap after a digit-hyphen, so this proves nothing"
    assert find_missing_text(pdf, wrapped, template) == []


def test_two_renders_produce_identical_bytes() -> None:
    invoice = generate_ground_truth(SEED, 0, load_catalogue()).invoice
    html = render_html(invoice, TEMPLATES[0], LABELS)
    with open_browser_page() as page:
        first = print_pdf(page, html)
    time.sleep(TIMESTAMP_RESOLUTION_SECONDS)  # so Chromium stamps a different time
    with open_browser_page() as page:
        second = print_pdf(page, html)
    assert first != second, "raw PDFs should differ, or this test proves nothing"
    assert normalise_pdf(first) == normalise_pdf(second)


def test_normalising_keeps_the_pdf_valid_and_its_length() -> None:
    invoice = generate_ground_truth(SEED, 0, load_catalogue()).invoice
    template = TEMPLATES[0]
    with open_browser_page() as page:
        raw = print_pdf(page, render_html(invoice, template, LABELS))
    normalised = normalise_pdf(raw)
    assert len(normalised) == len(raw)
    assert find_missing_text(normalised, invoice, template) == []


def test_normalising_only_changes_timestamps() -> None:
    pdf = b"%PDF /CreationDate (D:20261107075521+00'00') /Title (D:2026) /Count 12345678901234"
    assert normalise_pdf(pdf) == (
        b"%PDF /CreationDate (D:20000101000000+00'00') /Title (D:2026) /Count 12345678901234"
    )


def test_text_check_catches_a_wrong_total(out_dir: Path, manifest: Manifest) -> None:
    entry = clean_entries(manifest)[0]
    truth = GroundTruth.model_validate_json((out_dir / entry.ground_truth).read_bytes())
    tampered = truth.invoice.model_copy(update={"total_amount": truth.invoice.total_amount + 1})
    pdf = (out_dir / entry.path).read_bytes()
    assert find_missing_text(pdf, tampered, entry.template) == [str(tampered.total_amount)]


def test_pdf_metadata_holds_only_the_fixed_timestamp(out_dir: Path, manifest: Manifest) -> None:
    fixed = FIXED_PDF_TIMESTAMP.decode()
    with pypdfium2.PdfDocument(out_dir / clean_entries(manifest)[0].path) as document:
        metadata = document.get_metadata_dict()
    assert metadata["CreationDate"].startswith(fixed)
    assert metadata["ModDate"].startswith(fixed)


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
