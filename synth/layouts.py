"""How a synthetic document looks: its layout and the wording of its field labels."""

import random
from typing import Literal, NamedTuple

from pydantic import BaseModel

# How the main dataset (seed 42, built 2026-11-07) used a layout. Frozen: regenerating it
# must give the same documents in the same splits, whatever is held out today.
MainDatasetRole = Literal["split", "held_out", "absent"]


class Layout(NamedTuple):
    name: str
    # Never day/month-ambiguous: the date in the ground truth must be recoverable from the page.
    date_format: str
    can_span_pages: bool = False
    # Held out now: never in dev or test, and scored as the unseen group.
    held_out: bool = False
    # "split": rotated into dev and test; "held_out": rotated into test_unseen_layout;
    # "absent": added later, generated in its own folder (synth.generate --held-out).
    main_dataset: MainDatasetRole = "split"


LAYOUTS = (
    Layout("classic", "%d %B %Y"),
    Layout("modern", "%Y-%m-%d"),
    Layout("compact", "%d-%b-%Y"),
    Layout("airy", "%B %d, %Y", can_span_pages=True),
    Layout("sidebar", "%d %b %Y", held_out=True, main_dataset="held_out"),
    Layout("boxed", "%d/%b/%Y"),
    # Seen since 2026-11-17: its test failures were read to design the party boundary
    # check (Salik's holdout decision). The main dataset still has it in test_unseen_layout.
    Layout("ledger", "%b %d, %Y", can_span_pages=True, main_dataset="held_out"),
    Layout("summary", "%d.%b.%Y"),
    # Replaces ledger as the held-out layout printing a party on one line (2026-11-17).
    Layout("manifest", "%d-%B-%Y", held_out=True, main_dataset="absent"),
)
TEMPLATES = tuple(layout.name for layout in LAYOUTS)
UNSEEN_TEMPLATES = tuple(layout.name for layout in LAYOUTS if layout.held_out)
MULTI_PAGE_TEMPLATES = tuple(layout.name for layout in LAYOUTS if layout.can_span_pages)
MAIN_TEMPLATES = tuple(layout.name for layout in LAYOUTS if layout.main_dataset != "absent")
MAIN_HELD_OUT_TEMPLATES = tuple(
    layout.name for layout in LAYOUTS if layout.main_dataset == "held_out"
)
DATE_FORMATS = {layout.name: layout.date_format for layout in LAYOUTS}


class Labels(BaseModel):
    """The wording one document uses for its fields."""

    document_title: str
    invoice_number: str
    invoice_date: str
    shipper: str
    buyer: str
    consignee: str
    notify_party: str
    incoterm: str
    currency: str
    description: str
    hs_code: str
    quantity: str
    unit: str
    unit_price: str
    amount: str
    total: str
    net_weight: str
    gross_weight: str


# Wording that could mean a different field is left out: "Ref No" isn't always the invoice
# number and "Payment Terms" isn't the Incoterm, so neither has a known right answer.
# Parties never share wording: "Seller" and "Supplier" can differ from the shipper, and
# "Importer" can mean the buyer or the consignee, so none of them is used.
LABEL_SYNONYMS: dict[str, tuple[str, ...]] = {
    "document_title": (
        "Commercial Invoice",
        "Invoice",
        "Export Invoice",
        "Tax Invoice",
        "Sales Invoice",
    ),
    "invoice_number": (
        "Invoice No",
        "Invoice No.",
        "Inv #",
        "Invoice #",
        "Bill No",
        "Invoice Number",
        "Inv. No.",
    ),
    "invoice_date": ("Date", "Invoice Date", "Inv. Date", "Dated", "Date of Issue"),
    "shipper": ("Shipper", "Exporter", "Shipper / Exporter", "Consignor", "Shipped By", "From"),
    "buyer": ("Buyer", "Sold To", "Bill To", "Purchaser", "Invoiced To", "Customer"),
    "consignee": ("Consignee", "Ship To", "Deliver To", "Consigned To", "Delivery Address"),
    "notify_party": ("Notify Party", "Notify", "Also Notify", "Notify Address"),
    "incoterm": ("Incoterm", "Incoterms", "Terms of Delivery", "Delivery Terms", "Trade Terms"),
    "currency": ("Currency", "CCY", "Curr.", "Invoice Currency"),
    "description": (
        "Description of Goods",
        "Description",
        "Goods",
        "Item",
        "Particulars",
        "Commodity",
    ),
    "hs_code": ("HS Code", "H.S. Code", "HS", "Tariff Code", "PCT Code", "Commodity Code"),
    "quantity": ("Quantity", "Qty", "Qty.", "Quantity Shipped"),
    "unit": ("Unit", "UOM", "U/M", "Units"),
    "unit_price": ("Unit Price", "Rate", "Price", "Price per Unit", "Unit Rate"),
    "amount": ("Amount", "Value", "Line Total", "Total Price", "Ext. Value"),
    "total": ("Total", "Grand Total", "Invoice Total", "Total Amount", "Total Value"),
    "net_weight": ("Net Weight", "Net Wt", "N.W.", "Net Wt."),
    "gross_weight": ("Gross Weight", "Gross Wt", "G.W.", "Gross Wt."),
}


def template_for(index: int) -> str:
    """The main dataset's layout for a document: a rotation, so each is equally represented."""
    return MAIN_TEMPLATES[index % len(MAIN_TEMPLATES)]


def pick_labels(rng: random.Random) -> Labels:
    return Labels(**{field: rng.choice(LABEL_SYNONYMS[field]) for field in Labels.model_fields})
