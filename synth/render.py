"""Render invoices to PDF: Jinja2 HTML, printed by headless Chromium."""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pypdfium2
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from app.schemas.raw import RawInvoice
from synth.generator import COUNTRY_NAMES

TEMPLATE_DIR = Path(__file__).with_name("templates")
# Layout name -> date format. No day/month-ambiguous formats: ground truth must be recoverable.
DATE_FORMATS = {"classic": "%d %B %Y", "modern": "%Y-%m-%d", "compact": "%d-%b-%Y"}
TEMPLATES = tuple(DATE_FORMATS)

# Chromium writes /CreationDate and /ModDate as D:YYYYMMDDHHMMSS plus a UTC offset.
PDF_TIMESTAMP = re.compile(rb"D:\d{14}")
FIXED_PDF_TIMESTAMP = b"D:20000101000000"

# PDFium extracts a hyphen at a line wrap as this control character and joins the two lines.
PDFIUM_WRAPPED_HYPHEN = "\x02"


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


def template_for(index: int) -> str:
    """Rotate through layouts so each one is equally represented."""
    return TEMPLATES[index % len(TEMPLATES)]


def render_html(invoice: RawInvoice, template: str) -> str:
    return ENVIRONMENT.get_template(f"{template}.html.j2").render(
        invoice=invoice, date_format=DATE_FORMATS[template]
    )


@contextmanager
def open_browser_page() -> Iterator[Page]:
    """Yield one reusable page, because launching Chromium per document dominates run time."""
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
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


def find_missing_text(pdf: bytes, invoice: RawInvoice, template: str) -> list[str]:
    """Return ground-truth values absent from the PDF's text.

    A template bug here would silently corrupt every eval number, so generation checks
    each document. Comparison ignores case, commas and line wrapping, which vary by layout.
    """
    with pypdfium2.PdfDocument(pdf) as document:
        pages = [page.get_textpage().get_text_bounded() for page in document]
    printed = _normalise_text(" ".join(pages))
    expected = _expected_text(invoice, template)
    return [value for value in expected if _normalise_text(value) not in printed]


def _normalise_text(text: str) -> str:
    restored = text.replace(PDFIUM_WRAPPED_HYPHEN, "-")
    return " ".join(restored.replace(",", "").casefold().split())


def _expected_text(invoice: RawInvoice, template: str) -> list[str]:
    values = [
        invoice.invoice_number,
        invoice.invoice_date.strftime(DATE_FORMATS[template]),
        invoice.incoterm,
        invoice.currency,
        str(invoice.total_amount),
        str(invoice.net_weight_kg),
        str(invoice.gross_weight_kg),
    ]
    for party in (invoice.shipper, invoice.consignee):
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
