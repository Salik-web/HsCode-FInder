"""Load one dataset split: manifest entries, ground truth and file bytes."""

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from synth.generator import GroundTruth
from synth.manifest import Dataset, Manifest, ManifestEntry, Split

SYNTHETIC_MANIFEST = Path("synthetic") / "manifest.json"

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
    data_dir: Path, dataset: Dataset, split: Split, limit: int | None = None
) -> list[EvalDocument]:
    """Documents of one dataset and split, in id order, optionally only the first `limit`."""
    manifest_path = data_dir / SYNTHETIC_MANIFEST
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
            entry=entry,
            truth=GroundTruth.model_validate_json((root / entry.ground_truth).read_bytes()),
            path=root / entry.path,
        )
        for entry in entries
    ]
