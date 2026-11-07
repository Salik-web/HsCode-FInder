"""Dataset manifest and the deterministic dev/test split."""

from typing import Literal

from pydantic import BaseModel

from app.schemas.constants import SCHEMA_VERSION, SchemaVersion
from synth.augment import Transform
from synth.generator import derive_rng

Split = Literal["dev", "test"]
Dataset = Literal["synthetic_clean", "synthetic_messy"]

DEV_PERCENT = 70


class NotEnoughSourcesError(ValueError):
    """Too few eligible clean documents to build the requested messy set."""


class ManifestEntry(BaseModel):
    id: str
    dataset: Dataset
    split: Split
    path: str  # relative to the manifest
    ground_truth: str  # relative to the manifest
    sha256: str
    template: str
    pages: int
    source_id: str | None = None  # the clean document a messy one was made from
    transforms: list[Transform] = []


class Manifest(BaseModel):
    seed: int
    schema_version: SchemaVersion = SCHEMA_VERSION
    documents: list[ManifestEntry]


def _dev_count(total: int) -> int:
    return round(total * DEV_PERCENT / 100)


def assign_splits(document_ids: list[str], seed: int) -> dict[str, Split]:
    """Split ids exactly 70/30. Sorting first makes the result independent of input order."""
    shuffled = sorted(document_ids)
    derive_rng(seed, "split").shuffle(shuffled)
    dev_count = _dev_count(len(shuffled))
    return {
        document_id: "dev" if position < dev_count else "test"
        for position, document_id in enumerate(shuffled)
    }


def select_messy_sources(
    splits: dict[str, Split], eligible_ids: list[str], count: int, seed: int
) -> list[str]:
    """Pick clean documents to degrade, keeping the 70/30 ratio.

    A messy copy takes its source's split: a copy in dev of a clean document in test would
    leak test content into prompt tuning. So sampling happens inside each split.
    """
    rng = derive_rng(seed, "messy_sources")
    dev_target = _dev_count(count)
    selected: list[str] = []
    for split, target in (("dev", dev_target), ("test", count - dev_target)):
        pool = sorted(document_id for document_id in eligible_ids if splits[document_id] == split)
        if len(pool) < target:
            raise NotEnoughSourcesError(f"need {target} {split} sources, have {len(pool)}")
        selected += rng.sample(pool, target)
    return sorted(selected)
