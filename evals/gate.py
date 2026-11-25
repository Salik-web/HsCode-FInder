"""CI eval gate: replay a committed smoke set from cache and compare with the baseline.

uv run python -m evals.gate             # in CI on every push: no API keys, no network
uv run python -m evals.gate --refresh   # locally, after a deliberate change was evaluated

The fixtures in evals/fixtures are synthetic: about 20 documents (seen clean, seen photos,
unseen clean) with their ground truth, exactly the LLM answers they need, the
injected-error set, and the metrics they scored when committed (baseline.json).

Fixture answers are keyed on the request without its page images: PDFium renders a page
to different bytes on Linux (CI) and Windows, so the production cache key (which hashes
the images) can't be replayed across machines. The key still holds the model, call
options, prompt version, attempt, system prompt and user text with the text layer, so a
prompt or text-layer change fails the gate; an image-only preprocessing change doesn't.

The gate fails when
- a document can't be scored offline: its request changed (a prompt, preprocessing or
  model change), so the evals weren't re-run for it;
- critical-field accuracy drops by more than 2 points in any set and layout group;
- the guardrails catch fewer planted errors, or flag clean ground truth, than the baseline.
"""

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel, SecretStr

from app.config import Settings
from app.llm.cache import CacheEntry, CachingClient, replay
from app.llm.client import (
    LLMClient,
    LLMRequest,
    LLMResponse,
    NotCachedError,
    SchemaViolationError,
)
from app.llm.factory import build_client
from evals.dataset import load_documents
from evals.guardrails import guardrail_report_for
from evals.report import EvalReport
from evals.run import RunConfig, run_eval
from synth.generator import ANCHOR_DATE
from synth.inject import INJECTED_DIR
from synth.manifest import MANIFEST_NAME, SYNTHETIC_DIR, Dataset, Manifest, Split
from tariff.parse import load_hs_code_table

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BASELINE = FIXTURES / "baseline.json"
ANSWERS = FIXTURES / "llm_answers"
MAX_DROP_POINTS = 2.0
# (dataset, split, documents kept): seen clean, seen photos, unseen clean.
GATE_SETS: tuple[tuple[Dataset, Split, int], ...] = (
    ("synthetic_clean", "dev", 12),
    ("synthetic_messy", "dev", 4),
    ("synthetic_clean", "test_unseen_layout", 4),
)


class GroupScore(BaseModel):
    documents: int
    critical_field_accuracy: float | None


class SetScore(BaseModel):
    documents: int
    scored: int
    groups: dict[str, GroupScore]  # layout group -> score


class GateScores(BaseModel):
    extraction: dict[str, SetScore]  # "dataset/split" -> score
    caught: dict[str, int]  # error kind -> planted errors caught
    clean_flagged: dict[str, int]  # clean set -> documents with a false alarm


def gate_settings(data_dir: Path, cache_dir: Path) -> Settings:
    """Offline: the key only satisfies the client factory; no call is ever made."""
    return Settings(
        _env_file=None,
        data_dir=data_dir,
        cache_dir=cache_dir,
        groq_api_key=SecretStr("offline-eval-gate"),
        llm_concurrency=1,
    )


def offline_client(settings: Settings) -> CachingClient:
    client = build_client(settings, use_fallback=False, offline=True)
    if not isinstance(client, CachingClient):  # an offline stack is a cache and nothing else
        raise TypeError("expected the offline client to be a CachingClient")
    return client


def fixture_key(request: LLMRequest, model: str, call_options: dict[str, str]) -> str:
    request_without_images = request.model_dump(mode="json", exclude={"images"})
    parts = [model, sorted(call_options.items()), request_without_images]
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def _answer_path(store: Path, key: str) -> Path:
    return store / key[:2] / f"{key}.json"


class FixtureClient:
    """Serves committed answers by fixture key; anything else is a miss, never a call."""

    def __init__(self, model: str, call_options: dict[str, str], store: Path) -> None:
        self._model = model
        self._call_options = call_options
        self._store = store

    @property
    def model(self) -> str:
        return self._model

    def complete(self, request: LLMRequest) -> LLMResponse:
        path = _answer_path(self._store, fixture_key(request, self._model, self._call_options))
        if not path.is_file():
            raise NotCachedError(f"no fixture answer for attempt {request.attempt}")
        return replay(CacheEntry.model_validate_json(path.read_bytes()))


def score(data_dir: Path, answers: Path) -> GateScores:
    settings = gate_settings(data_dir, answers)
    cached = offline_client(settings)  # only for the configured model and call options
    client = FixtureClient(cached.model, cached.call_options, answers)
    extraction = {
        f"{dataset}/{split}": _set_score(_offline_eval(settings, dataset, split, client))
        for dataset, split, _ in GATE_SETS
    }
    guardrails = guardrail_report_for(data_dir)
    return GateScores(
        extraction=extraction,
        caught={k.kind: k.caught_by_check + k.caught_by_schema for k in guardrails.kinds},
        clean_flagged={c.name: c.flagged for c in guardrails.clean},
    )


def _offline_eval(
    settings: Settings, dataset: Dataset, split: Split, client: LLMClient
) -> EvalReport:
    config = RunConfig(
        prompt_version=settings.invoice_prompt_version,
        dataset=dataset,
        split=split,
        model=client.model,
        reasoning_effort=settings.primary_provider().reasoning_effort,
        as_of=ANCHOR_DATE,
        offline=True,
        hs_codes=load_hs_code_table(),
        max_images=settings.extraction_max_images(use_fallback=False),
    )
    documents = load_documents(settings.data_dir, dataset, split)
    with tempfile.TemporaryDirectory() as reports:
        report, _ = run_eval(documents, client, config, Path(reports), concurrency=1)
    return report


def _set_score(report: EvalReport) -> SetScore:
    """Per layout group; reporting splits carry no per-document records, only counts."""
    return SetScore(
        documents=report.meta.documents_total,
        scored=report.meta.documents_scored,
        groups={
            group: GroupScore(
                documents=sliced.summary.documents,
                critical_field_accuracy=sliced.summary.critical_field_accuracy,
            )
            for group, sliced in report.layout_groups.items()
            if sliced.summary.documents
        },
    )


def compare(current: GateScores, baseline: GateScores) -> list[str]:
    """Every way `current` is worse than `baseline`; empty means the gate passes."""
    failures = []
    for name, expected in baseline.extraction.items():
        got = current.extraction.get(name)
        if got is None or got.scored < got.documents or got.documents < expected.documents:
            failures.append(
                f"{name}: documents not scored offline. A prompt, preprocessing or model "
                "change wasn't evaluated: run the evals, then `python -m evals.gate --refresh`"
            )
            continue
        for group, before in expected.groups.items():
            after = got.groups.get(group)
            was = before.critical_field_accuracy
            now = None if after is None else after.critical_field_accuracy
            if was is not None and (now is None or (was - now) * 100 > MAX_DROP_POINTS):
                failures.append(
                    f"{name} {group}: critical-field accuracy {_pct(now)} vs baseline "
                    f"{_pct(was)} (more than {MAX_DROP_POINTS:g} points down)"
                )
    for kind, caught in baseline.caught.items():
        if current.caught.get(kind, 0) < caught:
            failures.append(f"guardrails: {kind} caught {current.caught.get(kind, 0)} < {caught}")
    for name, flagged in baseline.clean_flagged.items():
        now_flagged = current.clean_flagged.get(name)
        if now_flagged is None or now_flagged > flagged:
            failures.append(f"guardrails: {name} has {now_flagged} false alarms (was {flagged})")
    return failures


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


# --- refresh: rebuild the fixtures from data/ and the full LLM cache ------------------------


class _Recorder:
    """Answers from the full cache; remembers, per document, each entry and its fixture key."""

    def __init__(self, inner: CachingClient) -> None:
        self._inner = inner
        self.used: dict[str, list[tuple[Path, str]]] = {}

    @property
    def model(self) -> str:
        return self._inner.model

    def complete(self, request: LLMRequest) -> LLMResponse:
        entry = (
            self._inner.path_for(request),
            fixture_key(request, self._inner.model, self._inner.call_options),
        )
        try:
            response = self._inner.complete(request)
        except SchemaViolationError:  # a cached rejection is an answer too
            self.used.setdefault(request.document_sha256, []).append(entry)
            raise
        self.used.setdefault(request.document_sha256, []).append(entry)
        return response


def refresh(data_dir: Path, cache_dir: Path) -> GateScores:
    for stale in (FIXTURES / SYNTHETIC_DIR, FIXTURES / INJECTED_DIR, ANSWERS):
        shutil.rmtree(stale, ignore_errors=True)
    settings = gate_settings(data_dir, cache_dir)
    manifest = Manifest.model_validate_json((data_dir / SYNTHETIC_DIR / MANIFEST_NAME).read_bytes())
    kept_ids: set[str] = set()
    for dataset, split, keep in GATE_SETS:
        recorder = _Recorder(offline_client(settings))
        _offline_eval(settings, dataset, split, recorder)
        # Answered from the cache means scorable offline; keep the first few by id.
        in_split = sorted(
            (e for e in manifest.documents if e.dataset == dataset and e.split == split),
            key=lambda e: e.id,
        )
        chosen = [e for e in in_split if e.sha256 in recorder.used][:keep]
        kept_ids.update(e.id for e in chosen)
        for entry in chosen:
            for path, key in recorder.used[entry.sha256]:
                _copy(path, _answer_path(ANSWERS, key))
    kept = [entry for entry in manifest.documents if entry.id in kept_ids]
    for entry in kept:
        for relative in (entry.path, entry.ground_truth):
            _copy(data_dir / SYNTHETIC_DIR / relative, FIXTURES / SYNTHETIC_DIR / relative)
    subset = manifest.model_copy(update={"documents": kept})
    (FIXTURES / SYNTHETIC_DIR / MANIFEST_NAME).write_text(subset.model_dump_json(indent=2))
    shutil.copytree(data_dir / INJECTED_DIR, FIXTURES / INJECTED_DIR)
    scores = score(FIXTURES, ANSWERS)
    BASELINE.write_text(scores.model_dump_json(indent=2) + "\n")
    return scores


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def main() -> None:
    parser = argparse.ArgumentParser(description="CI eval gate (offline).")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="rebuild fixtures and baseline from DATA_DIR and the LLM cache (local only)",
    )
    args = parser.parse_args()
    if args.refresh:
        local = Settings()
        scores = refresh(local.data_dir, local.cache_dir)
        print(f"Fixtures and baseline refreshed:\n{scores.model_dump_json(indent=2)}")
        return
    baseline = GateScores.model_validate_json(BASELINE.read_bytes())
    current = score(FIXTURES, ANSWERS)
    for name, got in current.extraction.items():
        groups = ", ".join(
            f"{group} {_pct(g.critical_field_accuracy)} (n={g.documents})"
            for group, g in got.groups.items()
        )
        print(f"  {name:38} scored {got.scored}/{got.documents}  critical: {groups}")
    print(f"  guardrails caught {current.caught}; clean flagged {current.clean_flagged}")
    failures = compare(current, baseline)
    if failures:
        print("EVAL GATE FAILED:\n- " + "\n- ".join(failures))
        sys.exit(1)
    print("Eval gate passed.")


if __name__ == "__main__":
    main()
