"""Render invoices to PDF: Jinja2 HTML, printed by headless Chromium."""

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import pymupdf
from jinja2 import Environment, FileSystemLoader, StrictUndefined
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from app.schemas.raw import RawInvoice
from synth.generator import COUNTRY_NAMES

TEMPLATE_DIR = Path(__file__).with_name("templates")
# Layout name -> date format. No day/month-ambiguous formats: ground truth must be recoverable.
DATE_FORMATS = {"classic": "%d %B %Y", "modern": "%Y-%m-%d", "compact": "%d-%b-%Y"}
TEMPLATES = tuple(DATE_FORMATS)


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


def render_pdf(page: Page, html: str) -> bytes:
    page.set_content(html)
    return normalise_pdf(page.pdf(prefer_css_page_size=True, print_background=True))


def normalise_pdf(pdf: bytes) -> bytes:
    """Strip timestamps and file IDs, so identical input gives an identical SHA-256."""
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        document.set_metadata({})
        document.del_xml_metadata()
        return document.tobytes(garbage=4, deflate=True, no_new_id=True)


def count_pages(pdf: bytes) -> int:
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        return document.page_count


def find_missing_text(pdf: bytes, invoice: RawInvoice, template: str) -> list[str]:
    """Return ground-truth values absent from the PDF's text.

    A template bug here would silently corrupt every eval number, so generation checks
    each document. Comparison ignores case, commas and line wrapping, which vary by layout.
    """
    with pymupdf.open(stream=pdf, filetype="pdf") as document:
        printed = _normalise_text(" ".join(page.get_text() for page in document))
    expected = _expected_text(invoice, template)
    return [value for value in expected if _normalise_text(value) not in printed]


def _normalise_text(text: str) -> str:
    return " ".join(text.replace(",", "").casefold().split())


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
