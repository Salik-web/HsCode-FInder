"""End-to-end: real Chromium rendering and augmentation on a small dataset. No LLM involved."""

import hashlib
import random
import re
import time
from pathlib import Path

import pypdfium2
import pytest
from PIL import Image

from app.pipeline.preprocess import PDFIUM_WRAPPED_HYPHEN, clean_text_layer
from app.schemas.raw import RawInvoice
from synth import generate
from synth.augment import STRENGTH_RANGES, TRANSFORM_ORDER, augment_pdf
from synth.generate import (
    MANIFEST_NAME,
    NotHeldOutError,
    SharedSeedError,
    build_dataset,
    build_diagnostic,
    build_held_out,
    diagnostic_dir,
    held_out_dir,
)
from synth.generator import GroundTruth, generate_ground_truth, load_catalogue
from synth.layouts import (
    LABEL_SYNONYMS,
    MAIN_HELD_OUT_TEMPLATES,
    MAIN_TEMPLATES,
    MULTI_PAGE_TEMPLATES,
    TEMPLATES,
    UNSEEN_TEMPLATES,
    Labels,
    pick_labels,
    template_for,
)
from synth.manifest import (
    DIAGNOSTIC_SPLIT,
    UNSEEN_SPLIT,
    Manifest,
    ManifestEntry,
    assign_splits,
    layout_group_for,
)
from synth.render import (
    FALLBACK_FAMILY,
    FIXED_PDF_TIMESTAMP,
    FONT_FACES,
    TEMPLATE_DIR,
    count_pages,
    find_missing_text,
    normalise_pdf,
    normalise_printed_text,
    open_browser_page,
    print_pdf,
    render_html,
    render_pdf,
    with_bundled_fonts,
)

SEED = 42
N_CLEAN = len(MAIN_TEMPLATES)  # one document per main-dataset layout
N_MESSY = 3
TIMESTAMP_RESOLUTION_SECONDS = 1.1
# Long enough to wrap inside the digit run even in the widest layout (`manifest`, a
# landscape party cell).
DIGIT_HYPHEN_ADDRESS = (
    f"Gewerbepark {'-'.join(['12', '34', '56', '78', '90'] * 12)} 58635 Lobenstein"
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


def test_every_main_layout_appears_in_the_dataset(manifest: Manifest) -> None:
    assert {entry.template for entry in clean_entries(manifest)} == set(MAIN_TEMPLATES)
    assert "manifest" not in MAIN_TEMPLATES


# The main dataset as built on 2026-11-07 (seed 42): regenerating it must give the same
# layout per index and the same splits, or dev and test documents would trade places.
ORIGINAL_ROTATION = (
    "classic",
    "modern",
    "compact",
    "airy",
    "sidebar",
    "boxed",
    "ledger",
    "summary",
)


def test_the_main_dataset_rotation_and_held_out_list_are_frozen() -> None:
    assert MAIN_TEMPLATES == ORIGINAL_ROTATION
    assert [template_for(index) for index in range(16)] == list(ORIGINAL_ROTATION) * 2
    assert MAIN_HELD_OUT_TEMPLATES == ("sidebar", "ledger")
    templates = {f"inv_{index:05d}": template_for(index) for index in range(100)}
    splits = assign_splits(templates, SEED)
    for doc_id, template in templates.items():
        assert (splits[doc_id] == UNSEEN_SPLIT) == (template in MAIN_HELD_OUT_TEMPLATES)


def test_ledger_is_seen_and_manifest_held_out_from_2026_11_17() -> None:
    assert UNSEEN_TEMPLATES == ("sidebar", "manifest")
    assert layout_group_for("ledger") == "seen"
    assert layout_group_for("manifest") == "unseen"


def test_main_dataset_splits_by_its_own_list_and_groups_by_today(manifest: Manifest) -> None:
    assert manifest.unseen_templates == list(UNSEEN_TEMPLATES)
    for entry in manifest.documents:
        assert (entry.split == UNSEEN_SPLIT) == (entry.template in MAIN_HELD_OUT_TEMPLATES)
        assert (entry.layout_group == "unseen") == (entry.template in UNSEEN_TEMPLATES)


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


# PDFium separates wrapped lines with CR LF.
PDFIUM_LINE_BREAK = "\r\n"
BOX_HTML = (
    '<!DOCTYPE html><html><head><style>div {{ font-family: "Bundled Liberation Mono"; '
    "font-size: 12pt; width: {width}ch; }}</style></head><body><div>{text}</div></body></html>"
)


def pdfium_text_in_a_box(text: str, width_ch: int) -> str:
    """PDFium's text for `text` printed in a fixed-width box in the bundled monospace font.

    A fixed width in a font shipped with the repo decides the line wrap, so it's the same on
    every machine; it used to depend on a layout's column and the OS's fonts.
    """
    html = with_bundled_fonts(BOX_HTML.format(width=width_ch, text=text))
    with open_browser_page() as page:
        pdf = print_pdf(page, html)
    with pypdfium2.PdfDocument(pdf) as document:
        return document[0].get_textpage().get_text_bounded()


def test_text_check_handles_a_hyphen_at_a_line_wrap() -> None:
    """Regression: invoice 91's address wrapped at its hyphen, which PDFium turns into a
    control character joining the lines."""
    text = pdfium_text_in_a_box("H.No. 098 Prasad Ganj, Anantapuram-986086", width_ch=13)
    assert f"Anantapuram{PDFIUM_WRAPPED_HYPHEN}986086" in text, "the box no longer wraps there"
    assert "anantapuram-986086" in normalise_printed_text(text)
    assert "Anantapuram-986086" in clean_text_layer(text)


def test_text_check_handles_a_hyphen_kept_at_a_digit_line_wrap() -> None:
    """After a digit, PDFium keeps the hyphen and the line break instead."""
    text = pdfium_text_in_a_box("Plot 1234-5678", width_ch=6)
    assert f"1234-{PDFIUM_LINE_BREAK}5678" in text, "the box no longer wraps there"
    assert "plot 1234-5678" in normalise_printed_text(text)
    assert "1234-5678" in clean_text_layer(text)


@pytest.mark.parametrize(
    ("pdfium_output", "comparable"),
    [
        (
            f"Prasad Ganj, Anantapuram{PDFIUM_WRAPPED_HYPHEN}986086",
            "prasad ganj anantapuram-986086",
        ),
        (
            f"Gewerbepark 12-34-{PDFIUM_LINE_BREAK}56-78 Lobenstein",
            "gewerbepark 12-34-56-78 lobenstein",
        ),
        (f"INV-2026-17447{PDFIUM_LINE_BREAK}Total 7,423.03", "inv-2026-17447 total 7423.03"),
    ],
)
def test_printed_text_normaliser_undoes_both_pdfium_hyphen_quirks(
    pdfium_output: str, comparable: str
) -> None:
    assert normalise_printed_text(pdfium_output) == comparable


# PostScript names of the bundled fonts as a PDF embeds them: "Carlito-Regular", and
# "LiberationMono" for LiberationMono-Regular.ttf.
BUNDLED_POSTSCRIPT_NAMES = {
    name
    for face in FONT_FACES
    for name in (Path(face.file).stem, Path(face.file).stem.removesuffix("-Regular"))
}
GENERIC_FAMILIES = {"serif", "sans-serif", "monospace"}
EMBEDDED_FONT_NAME = re.compile(rb"/(?:BaseFont|FontName)\s*/[A-Z]{6}\+([A-Za-z0-9-]+)")


@pytest.mark.parametrize("template", TEMPLATES)
def test_templates_name_only_bundled_fonts(template: str) -> None:
    """No OS font is named, so none can be picked; the bundled fallback is always there."""
    source = (TEMPLATE_DIR / f"{template}.html.j2").read_text(encoding="utf-8")
    stacks = re.findall(r"font-family:\s*([^;]+);", source)
    assert stacks
    bundled = {face.family for face in FONT_FACES}
    for stack in stacks:
        families = [family.strip().strip('"') for family in stack.split(",")]
        assert families[0] in bundled
        assert FALLBACK_FAMILY in families
        assert set(families) <= bundled | GENERIC_FAMILIES


@pytest.mark.parametrize("template", TEMPLATES)
def test_rendered_pdfs_embed_only_bundled_fonts(template: str) -> None:
    """The real guard: Chromium embeds what it drew with, so an OS font would show here."""
    invoice = generate_ground_truth(SEED, 0, load_catalogue()).invoice
    with open_browser_page() as page:
        pdf = render_pdf(page, render_html(invoice, template, LABELS))
    embedded = {name.decode() for name in EMBEDDED_FONT_NAME.findall(pdf)}
    assert embedded
    assert embedded <= BUNDLED_POSTSCRIPT_NAMES


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


DIAGNOSTIC_SEED = 7


@pytest.fixture(scope="module")
def diagnostic(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Manifest]:
    path = tmp_path_factory.mktemp("diagnostic")
    return path, build_diagnostic("ledger", 3, DIAGNOSTIC_SEED, path)


def test_diagnostic_batch_is_one_layout_outside_every_split(
    diagnostic: tuple[Path, Manifest],
) -> None:
    _, batch = diagnostic
    assert len(batch.documents) == 3
    for entry in batch.documents:
        assert entry.template == "ledger"
        assert entry.split == DIAGNOSTIC_SPLIT
        assert entry.layout_group == "seen"  # ledger is seen since 2026-11-17
        assert entry.dataset == "synthetic_clean"
        assert entry.id.startswith("diag_")


def test_diagnostic_batch_shares_no_content_with_the_main_dataset(
    diagnostic: tuple[Path, Manifest], out_dir: Path, manifest: Manifest
) -> None:
    folder, batch = diagnostic

    def invoice_numbers(root: Path, entries: list[ManifestEntry]) -> set[str]:
        paths = [root / entry.ground_truth for entry in entries]
        truths = [GroundTruth.model_validate_json(path.read_bytes()) for path in paths]
        return {str(truth.invoice.invoice_number) for truth in truths}

    assert not {e.sha256 for e in batch.documents} & {e.sha256 for e in manifest.documents}
    main_numbers = invoice_numbers(out_dir, clean_entries(manifest))
    assert not invoice_numbers(folder, batch.documents) & main_numbers


def test_diagnostic_cli_refuses_the_main_dataset_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same seed and index would rebuild a dev or test invoice in the diagnostic layout."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    generate.main(["--n", "1", "--messy", "0", "--seed", str(DIAGNOSTIC_SEED)])
    with pytest.raises(SharedSeedError):
        generate.main(["--diagnostic", "ledger", "--n", "1", "--seed", str(DIAGNOSTIC_SEED)])
    generate.main(["--diagnostic", "ledger", "--n", "1", "--seed", str(DIAGNOSTIC_SEED + 1)])
    assert (diagnostic_dir(tmp_path, "ledger") / MANIFEST_NAME).exists()
    # The main dataset is left as it was.
    main = Manifest.model_validate_json((tmp_path / "synthetic" / MANIFEST_NAME).read_bytes())
    assert [entry.id for entry in main.documents] == ["inv_00000"]


HELD_OUT_SEED = 11


@pytest.fixture(scope="module")
def held_out(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Manifest]:
    path = tmp_path_factory.mktemp("held_out")
    return path, build_held_out("manifest", 3, 1, HELD_OUT_SEED, path)


def test_held_out_batch_is_unseen_test_documents_in_one_layout(
    held_out: tuple[Path, Manifest],
) -> None:
    folder, batch = held_out
    clean = clean_entries(batch)
    messy = messy_entries(batch)
    assert [entry.id for entry in clean] == ["held_00000", "held_00001", "held_00002"]
    assert len(messy) == 1
    assert messy[0].source_id in {entry.id for entry in clean}
    for entry in batch.documents:
        assert entry.template == "manifest"
        assert entry.split == UNSEEN_SPLIT
        assert entry.layout_group == "unseen"
        assert (folder / entry.path).exists()


def test_held_out_batch_shares_no_content_with_the_main_dataset(
    held_out: tuple[Path, Manifest], manifest: Manifest
) -> None:
    _, batch = held_out
    assert not {e.sha256 for e in batch.documents} & {e.sha256 for e in manifest.documents}


def test_a_held_out_batch_in_a_seen_layout_is_refused(tmp_path: Path) -> None:
    with pytest.raises(NotHeldOutError):
        build_held_out("ledger", 1, 0, HELD_OUT_SEED, tmp_path)


def test_held_out_cli_refuses_the_main_dataset_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    generate.main(["--n", "1", "--messy", "0", "--seed", str(HELD_OUT_SEED)])
    argv = ["--held-out", "manifest", "--n", "1", "--messy", "0", "--seed"]
    with pytest.raises(SharedSeedError):
        generate.main([*argv, str(HELD_OUT_SEED)])
    generate.main([*argv, str(HELD_OUT_SEED + 1)])
    assert (held_out_dir(tmp_path, "manifest") / MANIFEST_NAME).exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["--n", "3", "--seed", "1"],
        ["--diagnostic", "ledger", "--n", "2", "--messy", "0", "--seed", "1"],
        ["--diagnostic", "no_such_layout", "--n", "2", "--seed", "1"],
        ["--held-out", "manifest", "--n", "2", "--seed", "1"],
        ["--held-out", "ledger", "--n", "2", "--messy", "0", "--seed", "1"],
        ["--diagnostic", "ledger", "--held-out", "manifest", "--n", "2", "--seed", "1"],
    ],
)
def test_cli_rejects_a_messy_count_in_the_wrong_mode(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        generate.main(argv)


def test_cli_writes_to_the_configured_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    generate.main(["--n", "2", "--messy", "1", "--seed", "7"])
    assert (tmp_path / "synthetic" / MANIFEST_NAME).exists()
    assert "Generated 3 documents" in capsys.readouterr().out
