"""Validate an uploaded file and turn it into page images plus a cleaned text layer.

PDFium isn't thread-safe: call this from one thread only (see CLAUDE.md known issues).
"""

import hashlib
import io
import re
from typing import Literal

import pypdfium2
from PIL import ExifTags, Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel

from app.llm.client import ImageInput

MediaType = Literal["application/pdf", "image/jpeg", "image/png"]

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_PAGES = 5
RENDER_DPI = 150
PDF_POINTS_PER_INCH = 72
# Long side cap for photos; a page rendered at RENDER_DPI is already below it.
MAX_IMAGE_SIDE_PX = 2000
JPEG_QUALITY = 90

MAGIC_BYTES: dict[bytes, MediaType] = {
    b"%PDF-": "application/pdf",
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG\r\n\x1a\n": "image/png",
}
UPRIGHT = 1  # EXIF orientation value for "no rotation needed"

# PDFium quirks seen on our own PDFs (CLAUDE.md): a hyphen wrapped after a letter becomes
# \x02 with the lines joined; a hyphen wrapped after a digit keeps the hyphen and the break.
PDFIUM_WRAPPED_HYPHEN = "\x02"
DIGIT_HYPHEN_AT_LINE_BREAK = re.compile(r"(?<=\d)-[ \t]*\r?\n[ \t]*(?=\d)")
CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class InvalidDocumentError(ValueError):
    """The file can't be processed: wrong type, too big, too many pages or corrupt."""


class PreparedDocument(BaseModel):
    document_sha256: str
    media_type: MediaType
    pages: list[ImageInput]
    # A hint for the model, never the source of truth: it can be wrong where the page
    # looks right. None when the file has no text layer (photos, scans).
    text_layer: str | None


def prepare_document(file_bytes: bytes) -> PreparedDocument:
    """Check type, size and page count, then render pages and read the text layer."""
    if len(file_bytes) > MAX_FILE_BYTES:
        raise InvalidDocumentError(
            f"file is {len(file_bytes)} bytes; the limit is {MAX_FILE_BYTES}"
        )
    media_type = detect_media_type(file_bytes)
    sha256 = hashlib.sha256(file_bytes).hexdigest()
    if media_type == "application/pdf":
        pages, text_layer = _prepare_pdf(file_bytes)
    else:
        pages, text_layer = [_prepare_image(file_bytes, media_type)], None
    return PreparedDocument(
        document_sha256=sha256, media_type=media_type, pages=pages, text_layer=text_layer
    )


def detect_media_type(file_bytes: bytes) -> MediaType:
    """Trust the file's content, never its name or a client-supplied type."""
    for magic, media_type in MAGIC_BYTES.items():
        if file_bytes.startswith(magic):
            return media_type
    raise InvalidDocumentError("unsupported file type: only PDF, JPEG and PNG are accepted")


def clean_text_layer(text: str) -> str:
    """Undo PDFium's wrapped-hyphen damage and drop control characters."""
    restored = text.replace(PDFIUM_WRAPPED_HYPHEN, "-")
    rejoined = DIGIT_HYPHEN_AT_LINE_BREAK.sub("-", restored.replace("\r\n", "\n"))
    return CONTROL_CHARACTERS.sub("", rejoined).strip()


def _prepare_pdf(file_bytes: bytes) -> tuple[list[ImageInput], str | None]:
    try:
        document = pypdfium2.PdfDocument(file_bytes)
    except pypdfium2.PdfiumError as error:
        raise InvalidDocumentError(f"unreadable PDF: {error}") from error
    with document:
        if len(document) > MAX_PAGES:
            raise InvalidDocumentError(f"{len(document)} pages; the limit is {MAX_PAGES}")
        if len(document) == 0:
            raise InvalidDocumentError("PDF has no pages")
        pages = [_render_page(page) for page in document]
        texts = [clean_text_layer(page.get_textpage().get_text_bounded()) for page in document]
    return pages, _join_page_texts(texts)


def _render_page(page: pypdfium2.PdfPage) -> ImageInput:
    image = page.render(scale=RENDER_DPI / PDF_POINTS_PER_INCH).to_pil()
    return ImageInput(media_type="image/png", data=_encode(image, "image/png"))


def _join_page_texts(texts: list[str]) -> str | None:
    if not any(texts):
        return None
    return "\n\n".join(f"[page {number}]\n{text}" for number, text in enumerate(texts, 1))


def _prepare_image(file_bytes: bytes, media_type: MediaType) -> ImageInput:
    try:
        image = Image.open(io.BytesIO(file_bytes))
        image.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as error:
        raise InvalidDocumentError(f"unreadable image: {error}") from error
    orientation = image.getexif().get(ExifTags.Base.Orientation, UPRIGHT)
    if orientation == UPRIGHT and max(image.size) <= MAX_IMAGE_SIDE_PX:
        return ImageInput(media_type=media_type, data=file_bytes)  # nothing to change
    upright = ImageOps.exif_transpose(image)
    upright.thumbnail((MAX_IMAGE_SIDE_PX, MAX_IMAGE_SIDE_PX), Image.Resampling.LANCZOS)
    return ImageInput(media_type=media_type, data=_encode(upright, media_type))


def _encode(image: Image.Image, media_type: MediaType) -> bytes:
    buffer = io.BytesIO()
    if media_type == "image/jpeg":
        image.convert("RGB").save(buffer, format="JPEG", quality=JPEG_QUALITY)
    else:
        image.save(buffer, format="PNG")
    return buffer.getvalue()
