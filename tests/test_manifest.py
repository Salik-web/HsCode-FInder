from collections import Counter

import pytest

from synth.generate import document_id
from synth.manifest import NotEnoughSourcesError, assign_splits, select_messy_sources

SEED = 42


def make_ids(n: int) -> list[str]:
    return [document_id(index) for index in range(n)]


def test_split_is_exactly_seventy_thirty() -> None:
    counts = Counter(assign_splits(make_ids(100), SEED).values())
    assert counts == {"dev": 70, "test": 30}


@pytest.mark.parametrize(
    ("n", "dev", "test"),
    [(1, 1, 0), (2, 1, 1), (3, 2, 1), (10, 7, 3)],
)
def test_split_handles_small_datasets(n: int, dev: int, test: int) -> None:
    counts = Counter(assign_splits(make_ids(n), SEED).values())
    assert counts["dev"] == dev
    assert counts["test"] == test


def test_split_of_empty_dataset_is_empty() -> None:
    assert assign_splits([], SEED) == {}


def test_every_document_gets_exactly_one_split() -> None:
    ids = make_ids(100)
    assert sorted(assign_splits(ids, SEED)) == ids


def test_split_is_deterministic() -> None:
    assert assign_splits(make_ids(100), SEED) == assign_splits(make_ids(100), SEED)


def test_split_ignores_input_order() -> None:
    ids = make_ids(100)
    assert assign_splits(ids, SEED) == assign_splits(list(reversed(ids)), SEED)


def test_split_changes_with_seed() -> None:
    assert assign_splits(make_ids(100), 1) != assign_splits(make_ids(100), 2)


def test_split_is_shuffled_not_sequential() -> None:
    splits = assign_splits(make_ids(100), SEED)
    first_seventy = [splits[doc_id] for doc_id in make_ids(100)[:70]]
    assert "test" in first_seventy


def test_messy_sources_keep_the_ratio() -> None:
    ids = make_ids(100)
    splits = assign_splits(ids, SEED)
    sources = select_messy_sources(splits, ids, 30, SEED)
    assert Counter(splits[source] for source in sources) == {"dev": 21, "test": 9}


def test_messy_sources_are_unique() -> None:
    ids = make_ids(100)
    sources = select_messy_sources(assign_splits(ids, SEED), ids, 30, SEED)
    assert len(sources) == len(set(sources)) == 30


def test_messy_sources_are_deterministic() -> None:
    ids = make_ids(100)
    splits = assign_splits(ids, SEED)
    assert select_messy_sources(splits, ids, 30, SEED) == select_messy_sources(
        splits, ids, 30, SEED
    )


def test_messy_sources_only_use_eligible_documents() -> None:
    ids = make_ids(100)
    splits = assign_splits(ids, SEED)
    eligible = ids[::2]
    assert set(select_messy_sources(splits, eligible, 10, SEED)) <= set(eligible)


def test_zero_messy_sources_is_allowed() -> None:
    ids = make_ids(10)
    assert select_messy_sources(assign_splits(ids, SEED), ids, 0, SEED) == []


@pytest.mark.parametrize("n", range(1, 40))
def test_every_document_can_be_a_messy_source(n: int) -> None:
    ids = make_ids(n)
    splits = assign_splits(ids, SEED)
    assert sorted(select_messy_sources(splits, ids, n, SEED)) == ids


def test_too_few_eligible_sources_raises() -> None:
    ids = make_ids(100)
    splits = assign_splits(ids, SEED)
    with pytest.raises(NotEnoughSourcesError):
        select_messy_sources(splits, ids[:5], 30, SEED)
