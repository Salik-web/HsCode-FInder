import random
from collections import Counter
from datetime import date

import pytest

from app.schemas.raw import RawInvoice
from synth.generator import generate_ground_truth, load_catalogue
from synth.layouts import (
    DATE_FORMATS,
    LABEL_SYNONYMS,
    LAYOUTS,
    MULTI_PAGE_TEMPLATES,
    TEMPLATES,
    UNSEEN_TEMPLATES,
    Labels,
    pick_labels,
    template_for,
)
from synth.render import TEMPLATE_DIR, render_html

SEED = 42
TABLE_COLUMNS = ("description", "hs_code", "quantity", "unit", "unit_price", "amount")


def marker_labels() -> Labels:
    """Labels that can't collide with other text, so each one can be found in the HTML."""
    return Labels(**{field: f"@{field}@" for field in Labels.model_fields})


def invoice_with_hs_codes() -> RawInvoice:
    """An invoice that prints every optional block: HS codes and a notify party."""
    catalogue = load_catalogue()
    invoices = (generate_ground_truth(SEED, index, catalogue).invoice for index in range(50))
    return next(
        invoice
        for invoice in invoices
        if invoice.line_items[0].hs_code_on_doc and invoice.notify_party is not None
    )


def column_order(template: str) -> tuple[str, ...]:
    html = render_html(invoice_with_hs_codes(), template, marker_labels())
    return tuple(sorted(TABLE_COLUMNS, key=lambda field: html.index(f"@{field}@")))


def test_there_are_six_to_eight_layouts() -> None:
    assert 6 <= len(TEMPLATES) <= 8
    assert len(set(TEMPLATES)) == len(TEMPLATES)


def test_exactly_two_layouts_are_held_out() -> None:
    assert len(UNSEEN_TEMPLATES) == 2


def test_every_layout_has_a_template_file_and_no_file_is_orphaned() -> None:
    files = {path.name.removesuffix(".html.j2") for path in TEMPLATE_DIR.glob("*.html.j2")}
    assert files == set(TEMPLATES)


def test_two_page_invoices_exist_in_both_seen_and_unseen_layouts() -> None:
    assert set(MULTI_PAGE_TEMPLATES) - set(UNSEEN_TEMPLATES)
    assert set(MULTI_PAGE_TEMPLATES) & set(UNSEEN_TEMPLATES)


def test_layouts_are_balanced_in_rotation() -> None:
    counts = Counter(template_for(index) for index in range(len(TEMPLATES) * 5))
    assert set(counts.values()) == {5}


def test_held_out_layouts_have_a_column_order_no_seen_layout_uses() -> None:
    seen_orders = {column_order(t) for t in TEMPLATES if t not in UNSEEN_TEMPLATES}
    unseen_orders = [column_order(t) for t in UNSEEN_TEMPLATES]
    assert len(set(unseen_orders)) == len(unseen_orders)
    assert not seen_orders & set(unseen_orders)


def test_seen_layouts_cover_several_column_orders() -> None:
    seen_orders = {column_order(t) for t in TEMPLATES if t not in UNSEEN_TEMPLATES}
    assert len(seen_orders) >= 3


@pytest.mark.parametrize("layout", LAYOUTS, ids=lambda layout: layout.name)
def test_date_formats_are_never_day_month_ambiguous(layout: tuple) -> None:
    assert "%m" not in layout.date_format or layout.date_format.startswith("%Y")


def test_every_layout_writes_the_date_differently() -> None:
    sample = date(2026, 3, 4)
    assert len({sample.strftime(fmt) for fmt in DATE_FORMATS.values()}) == len(TEMPLATES)


@pytest.mark.parametrize("template", TEMPLATES)
def test_every_layout_prints_every_label(template: str) -> None:
    # casefold because some layouts print their labels in capitals.
    html = render_html(invoice_with_hs_codes(), template, marker_labels()).casefold()
    assert [field for field in Labels.model_fields if f"@{field}@" not in html] == []


@pytest.mark.parametrize("template", TEMPLATES)
def test_hs_column_is_hidden_when_no_code_is_printed(template: str) -> None:
    invoice = invoice_with_hs_codes()
    lines = [line.model_copy(update={"hs_code_on_doc": None}) for line in invoice.line_items]
    html = render_html(invoice.model_copy(update={"line_items": lines}), template, marker_labels())
    assert "@hs_code@" not in html


@pytest.mark.parametrize("template", TEMPLATES)
def test_notify_party_block_is_hidden_when_there_is_none(template: str) -> None:
    invoice = invoice_with_hs_codes().model_copy(update={"notify_party": None})
    assert "@notify_party@" not in render_html(invoice, template, marker_labels()).casefold()


@pytest.mark.parametrize("template", TEMPLATES)
def test_buyer_reference_replaces_the_buyer_details(template: str) -> None:
    invoice = invoice_with_hs_codes()
    distinct_buyer = invoice.buyer.model_copy(update={"name": "@buyer_name@"})
    invoice = invoice.model_copy(update={"buyer": distinct_buyer})
    html = render_html(invoice, template, marker_labels(), "@reference@")
    assert "@buyer@" in html.casefold()
    assert "@reference@" in html
    assert "@buyer_name@" not in html
    assert "@buyer_name@" in render_html(invoice, template, marker_labels())


def test_no_wording_is_shared_between_fields() -> None:
    owners: dict[str, str] = {}
    for field, options in LABEL_SYNONYMS.items():
        for option in options:
            key = option.casefold()
            assert key not in owners, f"{option!r} used by {owners.get(key)} and {field}"
            owners[key] = field


def test_every_label_field_has_several_distinct_synonyms() -> None:
    assert set(LABEL_SYNONYMS) == set(Labels.model_fields)
    for field, options in LABEL_SYNONYMS.items():
        assert len(options) >= 3, field
        assert len(set(options)) == len(options), field


@pytest.mark.parametrize(
    ("field", "synonyms"),
    [
        ("invoice_number", ("Invoice No", "Inv #", "Bill No")),
        ("buyer", ("Buyer", "Sold To", "Bill To")),
        ("consignee", ("Consignee", "Ship To", "Deliver To")),
        ("notify_party", ("Notify Party",)),
    ],
)
def test_requested_synonyms_are_available(field: str, synonyms: tuple[str, ...]) -> None:
    assert set(synonyms) <= set(LABEL_SYNONYMS[field])


def test_same_rng_gives_same_labels() -> None:
    assert pick_labels(random.Random(1)) == pick_labels(random.Random(1))


def test_labels_vary_between_documents() -> None:
    labels = [pick_labels(random.Random(seed)) for seed in range(200)]
    for field, options in LABEL_SYNONYMS.items():
        assert {getattr(label, field) for label in labels} == set(options), field
