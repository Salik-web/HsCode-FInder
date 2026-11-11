from collections import Counter

import pytest

from synth.generate import document_id
from synth.layouts import TEMPLATES, UNSEEN_TEMPLATES, template_for
from synth.manifest import (
    UNSEEN_SPLIT,
    NotEnoughSourcesError,
    assign_splits,
    layout_group_for,
    select_messy_sources,
)

SEED = 42


def make_templates(n: int) -> dict[str, str]:
    return {document_id(index): template_for(index) for index in range(n)}


def make_seen_templates(n: int) -> dict[str, str]:
    return {document_id(index): "classic" for index in range(n)}


def test_seen_layouts_split_exactly_seventy_thirty() -> None:
    counts = Counter(assign_splits(make_seen_templates(100), SEED).values())
    assert counts == {"dev": 70, "test": 30}


def test_full_dataset_counts() -> None:
    counts = Counter(assign_splits(make_templates(100), SEED).values())
    assert counts == {"dev": 53, "test": 23, UNSEEN_SPLIT: 24}


@pytest.mark.parametrize(
    ("n", "dev", "test"),
    [(1, 1, 0), (2, 1, 1), (3, 2, 1), (10, 7, 3)],
)
def test_split_handles_small_datasets(n: int, dev: int, test: int) -> None:
    counts = Counter(assign_splits(make_seen_templates(n), SEED).values())
    assert counts["dev"] == dev
    assert counts["test"] == test


def test_split_of_empty_dataset_is_empty() -> None:
    assert assign_splits({}, SEED) == {}


def test_dataset_of_only_held_out_layouts_has_no_dev_documents() -> None:
    templates = {document_id(index): UNSEEN_TEMPLATES[0] for index in range(5)}
    assert set(assign_splits(templates, SEED).values()) == {UNSEEN_SPLIT}


def test_every_document_gets_exactly_one_split() -> None:
    templates = make_templates(100)
    assert sorted(assign_splits(templates, SEED)) == sorted(templates)


@pytest.mark.parametrize("seed", range(20))
def test_held_out_layouts_never_reach_dev_or_test(seed: int) -> None:
    templates = make_templates(100)
    for doc_id, split in assign_splits(templates, seed).items():
        assert (split == UNSEEN_SPLIT) == (templates[doc_id] in UNSEEN_TEMPLATES)


def test_every_seen_layout_reaches_dev() -> None:
    templates = make_templates(100)
    splits = assign_splits(templates, SEED)
    in_dev = {templates[doc_id] for doc_id, split in splits.items() if split == "dev"}
    assert in_dev == set(TEMPLATES) - set(UNSEEN_TEMPLATES)


def test_every_seen_layout_reaches_test() -> None:
    templates = make_templates(100)
    splits = assign_splits(templates, SEED)
    in_test = {templates[doc_id] for doc_id, split in splits.items() if split == "test"}
    assert in_test == set(TEMPLATES) - set(UNSEEN_TEMPLATES)


def test_layout_group_follows_the_split() -> None:
    assert layout_group_for("dev") == "seen"
    assert layout_group_for("test") == "seen"
    assert layout_group_for(UNSEEN_SPLIT) == "unseen"


def test_split_is_deterministic() -> None:
    assert assign_splits(make_templates(100), SEED) == assign_splits(make_templates(100), SEED)


def test_split_ignores_input_order() -> None:
    templates = make_templates(100)
    reversed_templates = dict(reversed(templates.items()))
    assert assign_splits(templates, SEED) == assign_splits(reversed_templates, SEED)


def test_split_changes_with_seed() -> None:
    assert assign_splits(make_templates(100), 1) != assign_splits(make_templates(100), 2)


def test_split_is_shuffled_not_sequential() -> None:
    splits = assign_splits(make_seen_templates(100), SEED)
    first_seventy = [splits[document_id(index)] for index in range(70)]
    assert "test" in first_seventy


def test_messy_sources_keep_each_splits_share() -> None:
    templates = make_templates(100)
    splits = assign_splits(templates, SEED)
    sources = select_messy_sources(splits, list(templates), 30, SEED)
    assert Counter(splits[source] for source in sources) == {
        "dev": 16,
        "test": 7,
        UNSEEN_SPLIT: 7,
    }


def test_messy_sources_are_unique() -> None:
    templates = make_templates(100)
    sources = select_messy_sources(assign_splits(templates, SEED), list(templates), 30, SEED)
    assert len(sources) == len(set(sources)) == 30


def test_messy_sources_are_deterministic() -> None:
    templates = make_templates(100)
    splits = assign_splits(templates, SEED)
    ids = list(templates)
    assert select_messy_sources(splits, ids, 30, SEED) == select_messy_sources(
        splits, ids, 30, SEED
    )


def test_messy_sources_only_use_eligible_documents() -> None:
    templates = make_templates(100)
    splits = assign_splits(templates, SEED)
    eligible = list(templates)[::3]
    assert set(select_messy_sources(splits, eligible, 10, SEED)) <= set(eligible)


def test_zero_messy_sources_is_allowed() -> None:
    templates = make_templates(10)
    assert select_messy_sources(assign_splits(templates, SEED), list(templates), 0, SEED) == []


@pytest.mark.parametrize("n", range(1, 40))
def test_every_document_can_be_a_messy_source(n: int) -> None:
    templates = make_templates(n)
    splits = assign_splits(templates, SEED)
    assert sorted(select_messy_sources(splits, list(templates), n, SEED)) == sorted(templates)


def test_too_few_eligible_sources_raises() -> None:
    templates = make_templates(100)
    splits = assign_splits(templates, SEED)
    with pytest.raises(NotEnoughSourcesError):
        select_messy_sources(splits, list(templates)[:5], 30, SEED)
