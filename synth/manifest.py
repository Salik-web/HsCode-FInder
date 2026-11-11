"""Dataset manifest and the deterministic split into dev, test and test_unseen_layout."""

from typing import Literal

from pydantic import BaseModel

from app.schemas.constants import SCHEMA_VERSION, SchemaVersion
from synth.augment import Transform
from synth.generator import derive_rng
from synth.layouts import UNSEEN_TEMPLATES

Split = Literal["dev", "test", "test_unseen_layout"]
LayoutGroup = Literal["seen", "unseen"]
Dataset = Literal["synthetic_clean", "synthetic_messy"]

UNSEEN_SPLIT: Split = "test_unseen_layout"
DEV_PERCENT = 70


class NotEnoughSourcesError(ValueError):
    """Too few eligible clean documents to build the requested messy set."""


class ManifestEntry(BaseModel):
    id: str
    dataset: Dataset
    split: Split
    layout_group: LayoutGroup  # every eval report scores these two groups separately
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
    unseen_templates: list[str] = list(UNSEEN_TEMPLATES)
    documents: list[ManifestEntry]


def layout_group_for(split: Split) -> LayoutGroup:
    return "unseen" if split == UNSEEN_SPLIT else "seen"


def _dev_count(total: int) -> int:
    return round(total * DEV_PERCENT / 100)


def assign_splits(templates: dict[str, str], seed: int) -> dict[str, Split]:
    """Map document id to split, given each document's template.

    Held-out layouts go straight to test_unseen_layout, so they can never reach dev. The
    rest split exactly 70/30; sorting first makes that independent of input order.
    """
    held_out = [doc_id for doc_id, template in templates.items() if template in UNSEEN_TEMPLATES]
    seen = sorted(doc_id for doc_id, template in templates.items() if doc_id not in held_out)
    derive_rng(seed, "split").shuffle(seen)
    dev_count = _dev_count(len(seen))
    splits: dict[str, Split] = dict.fromkeys(held_out, UNSEEN_SPLIT)
    for position, doc_id in enumerate(seen):
        splits[doc_id] = "dev" if position < dev_count else "test"
    return splits


def _messy_targets(splits: dict[str, Split], count: int) -> dict[Split, int]:
    """Spread `count` over the splits in the same proportions as the clean documents."""
    unseen_total = sum(split == UNSEEN_SPLIT for split in splits.values())
    unseen_target = round(count * unseen_total / len(splits))
    seen_target = count - unseen_target
    dev_target = _dev_count(seen_target)
    return {"dev": dev_target, "test": seen_target - dev_target, UNSEEN_SPLIT: unseen_target}


def select_messy_sources(
    splits: dict[str, Split], eligible_ids: list[str], count: int, seed: int
) -> list[str]:
    """Pick clean documents to degrade, keeping each split's share.

    A messy copy takes its source's split: a copy in dev of a clean document in test would
    leak test content into prompt tuning. So sampling happens inside each split.
    """
    rng = derive_rng(seed, "messy_sources")
    selected: list[str] = []
    for split, target in _messy_targets(splits, count).items():
        pool = sorted(doc_id for doc_id in eligible_ids if splits[doc_id] == split)
        if len(pool) < target:
            raise NotEnoughSourcesError(f"need {target} {split} sources, have {len(pool)}")
        selected += rng.sample(pool, target)
    return sorted(selected)
