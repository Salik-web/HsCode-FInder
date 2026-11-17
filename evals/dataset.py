"""Load one dataset split: manifest entries, ground truth and file bytes."""

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from synth.generator import GroundTruth
from synth.manifest import (
    MANIFEST_NAME,
    SYNTHETIC_DIR,
    Dataset,
    Manifest,
    ManifestEntry,
    Split,
    layout_group_for,
)

# How the buyer appears on the page: a reference to the consignee ("Same as consignee"),
# the consignee's details printed again, or a different company.
BuyerForm = Literal["reference", "repeated", "distinct"]


class DatasetFileMismatchError(RuntimeError):
    """A file's bytes don't match its manifest hash: the dataset is stale or edited."""


class EvalDocument(BaseModel):
    entry: ManifestEntry
    truth: GroundTruth
    path: Path

    @property
    def buyer_form(self) -> BuyerForm:
        if self.truth.buyer_reference is not None:
            return "reference"
        if self.truth.invoice.buyer == self.truth.invoice.consignee:
            return "repeated"
        return "distinct"

    def read_verified_bytes(self) -> bytes:
        data = self.path.read_bytes()
        if hashlib.sha256(data).hexdigest() != self.entry.sha256:
            raise DatasetFileMismatchError(f"{self.entry.id}: file doesn't match the manifest")
        return data


def load_documents(
    data_dir: Path,
    dataset: Dataset,
    split: Split,
    limit: int | None = None,
    folder: str = SYNTHETIC_DIR,
) -> list[EvalDocument]:
    """Documents of one dataset and split, in id order, optionally only the first `limit`.

    `folder` under DATA_DIR holds the manifest: the main dataset, a diagnostic batch or a
    held-out layout's batch. The layout group is today's, not the one written when the
    manifest was made: ledger documents generated as unseen now score as seen.
    """
    manifest_path = data_dir / folder / MANIFEST_NAME
    manifest = Manifest.model_validate_json(manifest_path.read_bytes())
    root = manifest_path.parent
    entries = sorted(
        (
            entry
            for entry in manifest.documents
            if entry.dataset == dataset and entry.split == split
        ),
        key=lambda entry: entry.id,
    )
    if limit is not None:
        entries = entries[:limit]
    return [
        EvalDocument(
            entry=entry.model_copy(update={"layout_group": layout_group_for(entry.template)}),
            truth=GroundTruth.model_validate_json((root / entry.ground_truth).read_bytes()),
            path=root / entry.path,
        )
        for entry in entries
    ]
