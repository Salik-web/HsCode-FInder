import hashlib
import io
import random

import pytest
from PIL import Image

from app.pipeline.preprocess import (
    MAX_FILE_BYTES,
    MAX_IMAGE_SIDE_PX,
    MAX_PAGES,
    InvalidDocumentError,
    clean_text_layer,
    detect_media_type,
    first_pages,
    prepare_document,
)
from synth.generator import generate_ground_truth, load_catalogue
from synth.layouts import pick_labels
from synth.render import open_browser_page, print_pdf, render_html, render_pdf

EXIF_ORIENTATION_TAG = 0x0112
ROTATE_90_CLOCKWISE = 6


def image_pdf(pages: int) -> bytes:
    """A PDF of plain images: real pages, but no text layer."""
    images = [Image.new("RGB", (200, 280), "white") for _ in range(pages)]
    buffer = io.BytesIO()
    images[0].save(buffer, format="PDF", save_all=True, append_images=images[1:])
    return buffer.getvalue()


def text_pdf(page_texts: list[str]) -> bytes:
    """A PDF with one page, and one text layer, per text."""
    pages = "".join(f'<p style="break-after: page">{text}</p>' for text in page_texts[:-1])
    with open_browser_page() as page:
        return print_pdf(page, f"<html><body>{pages}<p>{page_texts[-1]}</p></body></html>")


def encode(image: Image.Image, image_format: str, **options: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=image_format, **options)
    return buffer.getvalue()


def image_size(data: bytes) -> tuple[int, int]:
    return Image.open(io.BytesIO(data)).size


# --- text layer ----------------------------------------------------------------------------


def test_hyphen_wrapped_after_a_letter_is_restored() -> None:
    assert clean_text_layer("Anantapuram\x02986086") == "Anantapuram-986086"


def test_hyphen_wrapped_after_a_digit_is_rejoined() -> None:
    assert clean_text_layer("Gewerbepark 12-34-\r\n56-78") == "Gewerbepark 12-34-56-78"


def test_ordinary_line_breaks_are_kept() -> None:
    assert clean_text_layer("Invoice No\r\nINV-1\nTotal 4") == "Invoice No\nINV-1\nTotal 4"


def test_a_hyphen_at_a_break_between_words_is_not_joined() -> None:
    assert clean_text_layer("Hot-\nrolled") == "Hot-\nrolled"


def test_control_characters_are_removed_but_tabs_kept() -> None:
    assert clean_text_layer("A\x00B\x07C\tD\x1f") == "ABC\tD"


def test_real_pdf_text_layer_comes_back_clean() -> None:
    """Regression for the PDFium quirk: an address that wraps at its hyphen in `compact`."""
    invoice = generate_ground_truth(42, 0, load_catalogue()).invoice
    shipper = invoice.shipper.model_copy(
        update={"address": "H.No. 098 Prasad Ganj, Anantapuram-986086", "country": "IN"}
    )
    labels = pick_labels(random.Random(42))
    with open_browser_page() as page:
        html = render_html(invoice.model_copy(update={"shipper": shipper}), "compact", labels)
        pdf = render_pdf(page, html)
    document = prepare_document(pdf)
    assert document.text_layer is not None
    assert "Anantapuram-986086" in document.text_layer
    assert "\x02" not in document.text_layer
    assert document.text_layer.startswith("[page 1]")


# --- file checks ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [b"GIF89a....", b"PK\x03\x04zip", b"plain text", b"", b"<html>%PDF-</html>"],
)
def test_unsupported_types_are_rejected(data: bytes) -> None:
    with pytest.raises(InvalidDocumentError, match="unsupported file type"):
        prepare_document(data)


def test_type_comes_from_content() -> None:
    assert detect_media_type(image_pdf(1)) == "application/pdf"
    assert detect_media_type(encode(Image.new("RGB", (5, 5)), "PNG")) == "image/png"
    assert detect_media_type(encode(Image.new("RGB", (5, 5)), "JPEG")) == "image/jpeg"


def test_oversized_files_are_rejected_before_parsing() -> None:
    with pytest.raises(InvalidDocumentError, match="limit"):
        prepare_document(b"%PDF-" + b"\x00" * MAX_FILE_BYTES)


def test_corrupt_pdf_is_rejected() -> None:
    with pytest.raises(InvalidDocumentError, match="unreadable PDF"):
        prepare_document(b"%PDF-1.7 this is not really a pdf")


def test_corrupt_image_is_rejected() -> None:
    with pytest.raises(InvalidDocumentError, match="unreadable image"):
        prepare_document(b"\x89PNG\r\n\x1a\n not really a png")


def test_page_cap_is_enforced() -> None:
    assert len(prepare_document(image_pdf(MAX_PAGES)).pages) == MAX_PAGES
    with pytest.raises(InvalidDocumentError, match="pages"):
        prepare_document(image_pdf(MAX_PAGES + 1))


# --- rendering -----------------------------------------------------------------------------


def test_two_page_pdf_gives_two_page_images_in_order() -> None:
    document = prepare_document(image_pdf(2))
    assert len(document.pages) == 2
    assert {page.media_type for page in document.pages} == {"image/png"}
    # 200 x 280 points at 150 DPI is 416.7 x 583.3 px; PDFium rounds up.
    width, height = image_size(document.pages[0].data)
    assert width == pytest.approx(200 * 150 / 72, abs=1)
    assert height == pytest.approx(280 * 150 / 72, abs=1)


def test_pdf_without_text_has_no_text_layer() -> None:
    assert prepare_document(image_pdf(1)).text_layer is None


def test_document_id_is_the_sha256_of_the_file() -> None:
    data = image_pdf(1)
    assert prepare_document(data).document_sha256 == hashlib.sha256(data).hexdigest()


def test_exif_rotation_is_applied() -> None:
    exif = Image.Exif()
    exif[EXIF_ORIENTATION_TAG] = ROTATE_90_CLOCKWISE
    photo = encode(Image.new("RGB", (300, 100), "white"), "JPEG", exif=exif)
    (page,) = prepare_document(photo).pages
    assert image_size(page.data) == (100, 300)
    assert page.media_type == "image/jpeg"


def test_upright_photo_within_limits_is_passed_through_unchanged() -> None:
    photo = encode(Image.new("RGB", (300, 100), "white"), "JPEG")
    document = prepare_document(photo)
    assert document.pages[0].data == photo
    assert document.text_layer is None


def test_large_photo_is_downscaled_keeping_its_shape() -> None:
    photo = encode(Image.new("RGB", (4000, 3000), "white"), "PNG")
    (page,) = prepare_document(photo).pages
    assert image_size(page.data) == (MAX_IMAGE_SIDE_PX, 1500)
    assert page.media_type == "image/png"


# --- the model's image limit -----------------------------------------------------------------


def test_first_pages_cuts_the_images_and_the_text_layer_together() -> None:
    document = prepare_document(text_pdf(["Alpha", "Bravo", "Charlie", "Delta terms"]))
    assert len(document.pages) == 4
    cut = first_pages(document, 3)
    assert cut.pages == document.pages[:3]
    assert cut.text_layer is not None
    assert "Charlie" in cut.text_layer
    assert "Delta" not in cut.text_layer
    assert cut.document_sha256 == document.document_sha256
    assert document.text_layer is not None
    assert "Delta" in document.text_layer  # the original keeps every page


@pytest.mark.parametrize("limit", [4, 5, None])
def test_first_pages_changes_nothing_within_the_limit(limit: int | None) -> None:
    document = prepare_document(image_pdf(4))
    assert first_pages(document, limit) is document
