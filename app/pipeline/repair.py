"""Repair near-miss JSON keys and drop unknown ones, before schema validation.

Models sometimes misspell a key (`" shipper"`, `"shiper"`) or add one nobody asked
for (`"document_type"`). A misspelt required key would otherwise fail the whole answer,
and an extra key carries nothing we score. So, per JSON object:

- a key the schema knows is kept;
- an unknown key is renamed to a schema key only when the match is strict and
  unambiguous: similarity >= KEY_MATCH_THRESHOLD, the target key is missing from the
  object, the runner-up is at least AMBIGUITY_MARGIN behind, and no other unknown key
  in the same object claims the same target;
- every other unknown key is dropped.

Every rename and drop is recorded, so reports can count them. Values are never changed,
and Pydantic still validates everything that remains.
"""

import difflib
import inspect
import logging
from typing import Any, get_args

from pydantic import BaseModel

logger = logging.getLogger(__name__)

KEY_MATCH_THRESHOLD = 0.85
AMBIGUITY_MARGIN = 0.05


class KeyRepairs(BaseModel):
    repaired: list[str]  # e.g. "buyer: ' country' -> 'country'"
    ignored: list[str]  # e.g. "document_type", "buyer.notes"

    def merge(self, other: "KeyRepairs") -> "KeyRepairs":
        return KeyRepairs(
            repaired=self.repaired + other.repaired, ignored=self.ignored + other.ignored
        )


NO_REPAIRS = KeyRepairs(repaired=[], ignored=[])


def key_similarity(candidate: str, known: str) -> float:
    """Case and surrounding whitespace never count against a key."""
    return difflib.SequenceMatcher(None, candidate.strip().casefold(), known).ratio()


def repair_keys(data: Any, model: type[BaseModel], path: str = "") -> tuple[Any, KeyRepairs]:
    """Return `data` with keys repaired or dropped against `model`, and what was done."""
    if not isinstance(data, dict):
        return data, NO_REPAIRS
    renamed, repairs = _repair_object(data, model, path)
    result: dict[str, Any] = {}
    for key, value in renamed.items():
        nested = _nested_model(model.model_fields[key].annotation)
        result[key], inner = _repair_value(value, nested, _join(path, key))
        repairs = repairs.merge(inner)
    return result, repairs


def _repair_value(value: Any, nested: type[BaseModel] | None, path: str) -> tuple[Any, KeyRepairs]:
    if nested is None:
        return value, NO_REPAIRS
    if isinstance(value, list):
        repairs = NO_REPAIRS
        items = []
        for index, item in enumerate(value):
            fixed, inner = repair_keys(item, nested, f"{path}[{index}]")
            items.append(fixed)
            repairs = repairs.merge(inner)
        return items, repairs
    return repair_keys(value, nested, path)


def _repair_object(
    data: dict[str, Any], model: type[BaseModel], path: str
) -> tuple[dict[str, Any], KeyRepairs]:
    known = list(model.model_fields)
    missing = [key for key in known if key not in data]
    unknown = [key for key in data if key not in model.model_fields]
    proposals = {key: _best_match(key, missing) for key in unknown}
    claimed = [target for target in proposals.values() if target is not None]
    renamed = {key: value for key, value in data.items() if key in model.model_fields}
    repaired: list[str] = []
    ignored: list[str] = []
    for key in unknown:
        target = proposals[key]
        if target is not None and claimed.count(target) == 1:
            renamed[target] = data[key]
            repaired.append(f"{path or '(top level)'}: {key!r} -> {target!r}")
        else:
            ignored.append(_join(path, key))
    for entry in repaired:
        logger.info("repaired JSON key %s", entry)
    if ignored:
        logger.info("ignored unknown JSON keys %s", ignored)
    # Keep schema order, so the repaired object reads like a well-formed answer.
    ordered = {key: renamed[key] for key in known if key in renamed}
    return ordered, KeyRepairs(repaired=repaired, ignored=ignored)


def _best_match(key: str, candidates: list[str]) -> str | None:
    """The single candidate `key` clearly means, or None."""
    scored = sorted(
        ((key_similarity(key, candidate), candidate) for candidate in candidates), reverse=True
    )
    if not scored or scored[0][0] < KEY_MATCH_THRESHOLD:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < AMBIGUITY_MARGIN:
        return None
    return scored[0][1]


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    """The model inside a field's type, e.g. RawParty in `RawParty | None` or a list of it."""
    if inspect.isclass(annotation) and issubclass(annotation, BaseModel):
        return annotation
    for argument in get_args(annotation):
        found = _nested_model(argument)
        if found is not None:
            return found
    return None


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def describe_allowed_keys(model: type[BaseModel]) -> str:
    """The exact key names a model accepts, for prompts. Generated, so it can't drift."""
    lines = [f"- top level: {', '.join(model.model_fields)}"]
    nested: dict[type[BaseModel], list[str]] = {}
    for name, field in model.model_fields.items():
        inner = _nested_model(field.annotation)
        if inner is not None:
            nested.setdefault(inner, []).append(name)
        elif field.description:
            lines.append(f"- {name}: {field.description}")
    for inner, owners in nested.items():
        lines.append(f"- inside {', '.join(owners)}: {', '.join(inner.model_fields)}")
    return "\n".join(lines)
