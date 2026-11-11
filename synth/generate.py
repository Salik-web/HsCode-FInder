"""CLI: generate the synthetic dataset.

uv run python -m synth.generate --n 100 --messy 30 --seed 42
"""

import argparse
import hashlib
import time
from collections import Counter
from pathlib import Path

from playwright.sync_api import Page

from app.config import Settings
from synth.augment import augment_pdf
from synth.generator import (
    CatalogueItem,
    derive_rng,
    generate_ground_truth,
    load_catalogue,
)
from synth.layouts import pick_labels, template_for
from synth.manifest import (
    Manifest,
    ManifestEntry,
    Split,
    assign_splits,
    layout_group_for,
    select_messy_sources,
)
from synth.render import (
    PrintedTextMismatchError,
    count_pages,
    find_missing_text,
    open_browser_page,
    render_html,
    render_pdf,
)

SYNTHETIC_DIR = "synthetic"
CLEAN_DIR = "clean"
MESSY_DIR = "messy"
MANIFEST_NAME = "manifest.json"


def document_id(index: int) -> str:
    return f"inv_{index:05d}"


def build_dataset(n: int, messy: int, seed: int, out_dir: Path) -> Manifest:
    """Write `n` clean and `messy` degraded documents plus the manifest to `out_dir`."""
    _prepare_folders(out_dir)
    catalogue = load_catalogue()
    splits = assign_splits({document_id(index): template_for(index) for index in range(n)}, seed)
    with open_browser_page() as page:
        clean = [
            _write_clean_document(page, index, seed, catalogue, splits, out_dir)
            for index in range(n)
        ]
    # Page one of a longer invoice would be missing lines that its ground truth still lists.
    single_page_ids = [entry.id for entry in clean if entry.pages == 1]
    sources = set(select_messy_sources(splits, single_page_ids, messy, seed))
    degraded = [
        _write_messy_document(entry, seed, out_dir) for entry in clean if entry.id in sources
    ]
    manifest = Manifest(seed=seed, documents=[*clean, *degraded])
    (out_dir / MANIFEST_NAME).write_bytes(manifest.model_dump_json(indent=2).encode())
    return manifest


def _prepare_folders(out_dir: Path) -> None:
    """Remove documents from earlier runs, so a smaller run leaves no stale files behind."""
    for folder in (out_dir / CLEAN_DIR, out_dir / MESSY_DIR):
        folder.mkdir(parents=True, exist_ok=True)
        for stale_file in folder.glob("inv_*"):
            stale_file.unlink()


def _write_clean_document(
    page: Page,
    index: int,
    seed: int,
    catalogue: list[CatalogueItem],
    splits: dict[str, Split],
    out_dir: Path,
) -> ManifestEntry:
    doc_id = document_id(index)
    template = template_for(index)
    truth = generate_ground_truth(seed, index, catalogue)
    labels = pick_labels(derive_rng(seed, "labels", index))
    pdf = render_pdf(page, render_html(truth.invoice, template, labels))
    missing = find_missing_text(pdf, truth.invoice, template)
    if missing:
        raise PrintedTextMismatchError(f"{doc_id} ({template}) doesn't show: {missing}")
    pdf_path = f"{CLEAN_DIR}/{doc_id}.pdf"
    truth_path = f"{CLEAN_DIR}/{doc_id}.json"
    (out_dir / pdf_path).write_bytes(pdf)
    (out_dir / truth_path).write_bytes(truth.model_dump_json(indent=2).encode())
    return ManifestEntry(
        id=doc_id,
        dataset="synthetic_clean",
        split=splits[doc_id],
        layout_group=layout_group_for(splits[doc_id]),
        path=pdf_path,
        ground_truth=truth_path,
        sha256=hashlib.sha256(pdf).hexdigest(),
        template=template,
        pages=count_pages(pdf),
    )


def _write_messy_document(source: ManifestEntry, seed: int, out_dir: Path) -> ManifestEntry:
    doc_id = f"{source.id}_messy"
    rng = derive_rng(seed, "augment", source.id)
    image, transforms = augment_pdf((out_dir / source.path).read_bytes(), rng)
    image_path = f"{MESSY_DIR}/{doc_id}.jpg"
    (out_dir / image_path).write_bytes(image)
    return ManifestEntry(
        id=doc_id,
        dataset="synthetic_messy",
        split=source.split,
        layout_group=source.layout_group,
        path=image_path,
        ground_truth=source.ground_truth,
        sha256=hashlib.sha256(image).hexdigest(),
        template=source.template,
        pages=1,
        source_id=source.id,
        transforms=transforms,
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the synthetic invoice dataset.")
    parser.add_argument("--n", type=int, required=True, help="number of clean documents")
    parser.add_argument("--messy", type=int, required=True, help="number of messy documents")
    parser.add_argument("--seed", type=int, required=True, help="random seed")
    args = parser.parse_args(argv)
    if args.n < 1:
        parser.error("--n must be at least 1")
    if not 0 <= args.messy <= args.n:
        parser.error("--messy must be between 0 and --n")
    return args


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    out_dir = Settings().data_dir / SYNTHETIC_DIR
    started = time.perf_counter()
    manifest = build_dataset(args.n, args.messy, args.seed, out_dir)
    elapsed = time.perf_counter() - started
    counts = Counter((entry.dataset, entry.split) for entry in manifest.documents)
    print(f"Generated {len(manifest.documents)} documents in {elapsed:.1f}s -> {out_dir}")
    for (dataset, split), count in sorted(counts.items()):
        print(f"  {dataset:16} {split:5} {count}")


if __name__ == "__main__":
    main()
