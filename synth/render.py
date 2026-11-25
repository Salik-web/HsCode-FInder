"""Render invoices to PDF: Jinja2 HTML, printed by headless Chromium."""

import base64
import re
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import NamedTuple

import pypdfium2
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from app.pipeline.preprocess import clean_text_layer
from app.schemas.raw import RawInvoice
from synth.generator import COUNTRY_NAMES
from synth.layouts import DATE_FORMATS, Labels

TEMPLATE_DIR = Path(__file__).with_name("templates")
FONT_DIR = Path(__file__).with_name("fonts")


class FontFace(NamedTuple):
    family: str
    file: str
    weight: int


# Open fonts bundled in synth/fonts (licences in synth/fonts/licenses), embedded in every
# page as data URIs. Templates name only these families and end with FALLBACK_FAMILY, so
# Chromium never substitutes an OS font: Windows, Linux CI and Docker print the same text
# with the same widths, and so the same line wraps. Metric-compatible with the Windows
# fonts the layouts were drawn with where an open one exists (Carlito: Calibri; Liberation:
# Times New Roman, Courier New; Selawik: Segoe UI; TeX Gyre Pagella: Palatino).
FALLBACK_FAMILY = "Bundled DejaVu Sans"
FONT_FACES: tuple[FontFace, ...] = (
    FontFace("Bundled Carlito", "Carlito-Regular.ttf", 400),
    FontFace("Bundled Carlito", "Carlito-Bold.ttf", 700),
    FontFace("Bundled Fira Sans", "FiraSans-Regular.ttf", 400),
    FontFace("Bundled Fira Sans", "FiraSans-Bold.ttf", 700),
    FontFace("Bundled Liberation Serif", "LiberationSerif-Regular.ttf", 400),
    FontFace("Bundled Liberation Serif", "LiberationSerif-Bold.ttf", 700),
    FontFace("Bundled Liberation Mono", "LiberationMono-Regular.ttf", 400),
    FontFace("Bundled Liberation Mono", "LiberationMono-Bold.ttf", 700),
    FontFace("Bundled DejaVu Sans", "DejaVuSans.ttf", 400),
    FontFace("Bundled DejaVu Sans", "DejaVuSans-Bold.ttf", 700),
    FontFace("Bundled DejaVu Sans Condensed", "DejaVuSansCondensed.ttf", 400),
    FontFace("Bundled DejaVu Sans Condensed", "DejaVuSansCondensed-Bold.ttf", 700),
    FontFace("Bundled Selawik", "Selawik-Light.ttf", 300),
    FontFace("Bundled Selawik", "Selawik-Regular.ttf", 400),
    FontFace("Bundled Selawik", "Selawik-Semibold.ttf", 600),
    FontFace("Bundled Selawik", "Selawik-Bold.ttf", 700),
    FontFace("Bundled TeX Gyre Pagella", "TeXGyrePagella-Regular.otf", 400),
    FontFace("Bundled TeX Gyre Pagella", "TeXGyrePagella-Bold.otf", 700),
)
# On Linux, Chromium hints fonts (FreeType), which snaps glyph advances to whole pixels:
# letters move, and PDFium then reads words and line breaks differently ("PrasadGanj").
# Without hinting, glyphs sit where the font's metrics put them, as on Windows.
CHROMIUM_ARGS = ["--font-render-hinting=none"]
FONT_FORMATS = {".ttf": ("font/ttf", "truetype"), ".otf": ("font/otf", "opentype")}

# Chromium writes /CreationDate and /ModDate as D:YYYYMMDDHHMMSS plus a UTC offset.
PDF_TIMESTAMP = re.compile(rb"D:\d{14}")
FIXED_PDF_TIMESTAMP = b"D:20000101000000"


class RendererUnavailableError(RuntimeError):
    """Chromium could not be started."""


class PrintedTextMismatchError(RuntimeError):
    """A rendered PDF doesn't show a value from its ground truth."""


def format_money(value: Decimal) -> str:
    return f"{value:,.2f}"


def format_quantity(value: Decimal) -> str:
    return f"{value:,}"


def _build_environment() -> Environment:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=True,  # Faker company names contain "&"
        undefined=StrictUndefined,
    )
    environment.filters["money"] = format_money
    environment.filters["quantity"] = format_quantity
    environment.filters["country_name"] = COUNTRY_NAMES.__getitem__
    return environment


ENVIRONMENT = _build_environment()


def render_html(
    invoice: RawInvoice, template: str, labels: Labels, buyer_reference: str | None = None
) -> str:
    """Render one invoice; `buyer_reference` replaces the buyer's details when given."""
    html = ENVIRONMENT.get_template(f"{template}.html.j2").render(
        invoice=invoice,
        labels=labels,
        buyer_reference=buyer_reference,
        date_format=DATE_FORMATS[template],
        show_hs=any(line.hs_code_on_doc for line in invoice.line_items),
    )
    return with_bundled_fonts(html)


def with_bundled_fonts(html: str) -> str:
    """Embed the bundled faces the page names, plus the fallback, as @font-face rules."""
    named = {face.family for face in FONT_FACES if f'"{face.family}"' in html}
    faces = [face for face in FONT_FACES if face.family in named | {FALLBACK_FAMILY}]
    css = "\n".join(_font_face_rule(face) for face in faces)
    return html.replace("<head>", f"<head>\n<style>\n{css}\n</style>", 1)


@cache
def _font_face_rule(face: FontFace) -> str:
    path = FONT_DIR / face.file
    media_type, css_format = FONT_FORMATS[path.suffix]
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return (
        f'@font-face {{ font-family: "{face.family}"; font-weight: {face.weight}; '
        f'src: url(data:{media_type};base64,{data}) format("{css_format}"); }}'
    )


@contextmanager
def open_browser_page() -> Iterator[Page]:
    """Yield one reusable page, because launching Chromium per document dominates run time."""
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(args=CHROMIUM_ARGS)
        except PlaywrightError as error:
            message = "Chromium not available. Run: uv run playwright install chromium"
            raise RendererUnavailableError(message) from error
        try:
            yield browser.new_page()
        finally:
            browser.close()


def print_pdf(page: Page, html: str) -> bytes:
    page.set_content(html)
    return page.pdf(prefer_css_page_size=True, print_background=True)


def render_pdf(page: Page, html: str) -> bytes:
    return normalise_pdf(print_pdf(page, html))


def normalise_pdf(pdf: bytes) -> bytes:
    """Fix Chromium's print timestamps, so identical input gives an identical SHA-256.

    The replacement has the same length, because a PDF's cross-reference table stores byte
    offsets that any change in length would invalidate.
    """
    return PDF_TIMESTAMP.sub(FIXED_PDF_TIMESTAMP, pdf)


def count_pages(pdf: bytes) -> int:
    with pypdfium2.PdfDocument(pdf) as document:
        return len(document)


def find_missing_text(
    pdf: bytes, invoice: RawInvoice, template: str, buyer_reference: str | None = None
) -> list[str]:
    """Return ground-truth values absent from the PDF's text.

    A template bug here would silently corrupt every eval number, so generation checks
    each document. Comparison ignores case, commas and line wrapping, which vary by layout.
    """
    with pypdfium2.PdfDocument(pdf) as document:
        pages = [page.get_textpage().get_text_bounded() for page in document]
    printed = normalise_printed_text(" ".join(pages))
    expected = _expected_text(invoice, template, buyer_reference)
    return [value for value in expected if normalise_printed_text(value) not in printed]


def normalise_printed_text(text: str) -> str:
    """Comparable form of PDF text: PDFium's wrapped-hyphen quirks undone (the pipeline's
    own cleanup), then case, commas and line wrapping ignored."""
    restored = clean_text_layer(text)
    collapsed = " ".join(restored.replace(",", "").casefold().split())
    return collapsed.replace("- ", "-")


def _expected_text(invoice: RawInvoice, template: str, buyer_reference: str | None) -> list[str]:
    values = [
        invoice.invoice_number,
        invoice.invoice_date.strftime(DATE_FORMATS[template]),
        invoice.incoterm,
        invoice.currency,
        str(invoice.total_amount),
        str(invoice.net_weight_kg),
        str(invoice.gross_weight_kg),
    ]
    parties = [invoice.shipper, invoice.consignee, invoice.notify_party]
    if buyer_reference is None:
        parties.append(invoice.buyer)
    else:
        values.append(buyer_reference)
    for party in parties:
        if party is not None:
            values += [party.name, party.address, COUNTRY_NAMES[party.country]]
    for line in invoice.line_items:
        values += [
            line.description,
            line.unit,
            str(line.quantity),
            str(line.unit_price),
            str(line.amount),
        ]
        if line.hs_code_on_doc:
            values.append(line.hs_code_on_doc)
    return values
