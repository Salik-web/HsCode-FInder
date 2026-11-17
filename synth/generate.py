"""CLI: generate the synthetic dataset, or a diagnostic batch in one layout.

uv run python -m synth.generate --n 100 --messy 30 --seed 42
uv run python -m synth.generate --diagnostic ledger --n 10 --seed 4207
uv run python -m synth.generate --held-out manifest --n 12 --messy 4 --seed 1117
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
from synth.layouts import TEMPLATES, UNSEEN_TEMPLATES, pick_labels, template_for
from synth.manifest import (
    DIAGNOSTIC_SPLIT,
    MANIFEST_NAME,
    SYNTHETIC_DIR,
    UNSEEN_SPLIT,
    Manifest,
    ManifestEntry,
    NotEnoughSourcesError,
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

CLEAN_DIR = "clean"
MESSY_DIR = "messy"
DATASET_PREFIX = "inv"
# Distinct ids, so a diagnostic document is never mistaken for a dev or test one.
DIAGNOSTIC_PREFIX = "diag"
DIAGNOSTIC_DIR_PREFIX = "diagnostic_"
# A held-out layout's own test documents, outside the main dataset (synth.layouts).
HELD_OUT_PREFIX = "held"
HELD_OUT_DIR_PREFIX = "heldout_"


class SharedSeedError(ValueError):
    """A diagnostic or held-out batch would repeat the main dataset's documents."""


class NotHeldOutError(ValueError):
    """A held-out batch in a seen layout would score as seen, so it would test nothing new."""


def document_id(index: int, prefix: str = DATASET_PREFIX) -> str:
    return f"{prefix}_{index:05d}"


def diagnostic_dir(data_dir: Path, template: str) -> Path:
    return data_dir / f"{DIAGNOSTIC_DIR_PREFIX}{template}"


def held_out_dir(data_dir: Path, template: str) -> Path:
    return data_dir / f"{HELD_OUT_DIR_PREFIX}{template}"


def build_dataset(n: int, messy: int, seed: int, out_dir: Path) -> Manifest:
    """Write `n` clean and `messy` degraded documents plus the manifest to `out_dir`."""
    _prepare_folders(out_dir, DATASET_PREFIX)
    catalogue = load_catalogue()
    splits = assign_splits({document_id(index): template_for(index) for index in range(n)}, seed)
    with open_browser_page() as page:
        clean = [
            _write_clean_document(
                page,
                document_id(index),
                index,
                template_for(index),
                splits[document_id(index)],
                seed,
                catalogue,
                out_dir,
            )
            for index in range(n)
        ]
    # Page one of a longer invoice would be missing lines that its ground truth still lists.
    single_page_ids = [entry.id for entry in clean if entry.pages == 1]
    sources = set(select_messy_sources(splits, single_page_ids, messy, seed))
    degraded = [
        _write_messy_document(entry, seed, out_dir) for entry in clean if entry.id in sources
    ]
    return _write_manifest(Manifest(seed=seed, documents=[*clean, *degraded]), out_dir)


def build_diagnostic(template: str, n: int, seed: int, out_dir: Path) -> Manifest:
    """Write `n` clean documents in one layout, outside every split, to study a failure.

    Content depends only on (seed, index), so with a seed other than the main dataset's
    no document repeats one in dev or test; the layout is forced, not rotated.
    """
    clean = _write_clean_batch(template, n, seed, out_dir, DIAGNOSTIC_PREFIX, DIAGNOSTIC_SPLIT)
    return _write_manifest(Manifest(seed=seed, documents=clean), out_dir)


def build_held_out(template: str, n: int, messy: int, seed: int, out_dir: Path) -> Manifest:
    """Write `n` clean and `messy` degraded test_unseen_layout documents in one held-out layout.

    For a layout added after the main dataset: its documents can't join that dataset
    without reshuffling it, so they get their own folder, ids and seed.
    """
    if template not in UNSEEN_TEMPLATES:
        raise NotHeldOutError(f"{template} isn't a held-out layout: {UNSEEN_TEMPLATES}")
    clean = _write_clean_batch(template, n, seed, out_dir, HELD_OUT_PREFIX, UNSEEN_SPLIT)
    # Page one of a longer invoice would be missing lines that its ground truth still lists.
    single_page = sorted(entry.id for entry in clean if entry.pages == 1)
    if len(single_page) < messy:
        raise NotEnoughSourcesError(f"need {messy} single-page sources, have {len(single_page)}")
    sources = set(derive_rng(seed, "messy_sources").sample(single_page, messy))
    degraded = [
        _write_messy_document(entry, seed, out_dir) for entry in clean if entry.id in sources
    ]
    return _write_manifest(Manifest(seed=seed, documents=[*clean, *degraded]), out_dir)


def _write_clean_batch(
    template: str, n: int, seed: int, out_dir: Path, prefix: str, split: Split
) -> list[ManifestEntry]:
    """`n` clean documents in one forced layout, written to `out_dir` with `prefix` ids."""
    _prepare_folders(out_dir, prefix)
    catalogue = load_catalogue()
    with open_browser_page() as page:
        return [
            _write_clean_document(
                page, document_id(index, prefix), index, template, split, seed, catalogue, out_dir
            )
            for index in range(n)
        ]


def _write_manifest(manifest: Manifest, out_dir: Path) -> Manifest:
    (out_dir / MANIFEST_NAME).write_bytes(manifest.model_dump_json(indent=2).encode())
    return manifest


def _prepare_folders(out_dir: Path, prefix: str) -> None:
    """Remove documents from earlier runs, so a smaller run leaves no stale files behind."""
    for folder in (out_dir / CLEAN_DIR, out_dir / MESSY_DIR):
        folder.mkdir(parents=True, exist_ok=True)
        for stale_file in folder.glob(f"{prefix}_*"):
            stale_file.unlink()


def _write_clean_document(
    page: Page,
    doc_id: str,
    index: int,
    template: str,
    split: Split,
    seed: int,
    catalogue: list[CatalogueItem],
    out_dir: Path,
) -> ManifestEntry:
    truth = generate_ground_truth(seed, index, catalogue)
    labels = pick_labels(derive_rng(seed, "labels", index))
    html = render_html(truth.invoice, template, labels, truth.buyer_reference)
    pdf = render_pdf(page, html)
    missing = find_missing_text(pdf, truth.invoice, template, truth.buyer_reference)
    if missing:
        raise PrintedTextMismatchError(f"{doc_id} ({template}) doesn't show: {missing}")
    pdf_path = f"{CLEAN_DIR}/{doc_id}.pdf"
    truth_path = f"{CLEAN_DIR}/{doc_id}.json"
    (out_dir / pdf_path).write_bytes(pdf)
    (out_dir / truth_path).write_bytes(truth.model_dump_json(indent=2).encode())
    return ManifestEntry(
        id=doc_id,
        dataset="synthetic_clean",
        split=split,
        layout_group=layout_group_for(template),
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


def refuse_main_dataset_seed(seed: int, data_dir: Path) -> None:
    """Same seed, same index -> the same invoice as a dev or test document."""
    manifest_path = data_dir / SYNTHETIC_DIR / MANIFEST_NAME
    if not manifest_path.exists():
        return
    main_seed = Manifest.model_validate_json(manifest_path.read_bytes()).seed
    if seed == main_seed:
        raise SharedSeedError(
            f"seed {seed} is the main dataset's: the batch would repeat its content"
        )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the synthetic invoice dataset.")
    parser.add_argument("--n", type=int, required=True, help="number of clean documents")
    parser.add_argument("--messy", type=int, help="number of messy documents (dataset only)")
    parser.add_argument("--seed", type=int, required=True, help="random seed")
    parser.add_argument(
        "--diagnostic",
        choices=TEMPLATES,
        help="write a clean batch in this layout to its own folder, outside every split",
    )
    parser.add_argument(
        "--held-out",
        choices=UNSEEN_TEMPLATES,
        help="write clean and messy test_unseen_layout documents in this layout to its own folder",
    )
    args = parser.parse_args(argv)
    if args.n < 1:
        parser.error("--n must be at least 1")
    if args.diagnostic and args.held_out:
        parser.error("choose --diagnostic or --held-out, not both")
    if args.diagnostic and args.messy is not None:
        parser.error("--messy doesn't apply to a diagnostic batch")
    if not args.diagnostic and args.messy is None:
        parser.error("--messy is required")
    if args.messy is not None and not 0 <= args.messy <= args.n:
        parser.error("--messy must be between 0 and --n")
    return args


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    data_dir = Settings().data_dir
    started = time.perf_counter()
    if args.diagnostic:
        refuse_main_dataset_seed(args.seed, data_dir)
        out_dir = diagnostic_dir(data_dir, args.diagnostic)
        manifest = build_diagnostic(args.diagnostic, args.n, args.seed, out_dir)
    elif args.held_out:
        refuse_main_dataset_seed(args.seed, data_dir)
        out_dir = held_out_dir(data_dir, args.held_out)
        manifest = build_held_out(args.held_out, args.n, args.messy, args.seed, out_dir)
    else:
        out_dir = data_dir / SYNTHETIC_DIR
        manifest = build_dataset(args.n, args.messy, args.seed, out_dir)
    elapsed = time.perf_counter() - started
    counts = Counter((entry.dataset, entry.split) for entry in manifest.documents)
    print(f"Generated {len(manifest.documents)} documents in {elapsed:.1f}s -> {out_dir}")
    for (dataset, split), count in sorted(counts.items()):
        print(f"  {dataset:16} {split:10} {count}")


if __name__ == "__main__":
    main()
