# CLAUDE.md — TradeDoc AI

Read this file and `docs/DESIGN.md` at the start of every session. Update the **Status** section at the end of every phase.

## What this project is
TradeDoc AI extracts structured data from trade documents (commercial invoices first, then packing lists and bills of lading) using vision LLMs, validates it with deterministic guardrails, suggests Pakistan Customs Tariff (HS/PCT) codes via RAG, and measures everything with an eval harness.

**The point of this project is evals and guardrails, not the UI.** Every change is judged by eval numbers.

Built by Salik (Full Stack AI Engineer) for his freight forwarding business (H & J Corporation) and as a public portfolio project.

## Status
- Current phase: **Phase 2 — Extraction + eval harness + tracing** (not started)
- Completed phases: Phase 1 — Foundation + synthetic data (approved by Salik, 2026-11-07)
- Latest eval baseline: none yet
- Known issues / open decisions:
  - `synth/catalogue.csv` holds 30 unverified placeholder rows: HS codes, units and `kg_per_unit` are all estimates. Replace with real PCT entries.
  - Unit prices are random, not tied to the goods. Accepted for now.
  - pypdfium2 has only been tested on our own Chromium PDFs. Verify it on scanned and real documents in Phase 3.
  - PDFium extracts a hyphen at a line wrap as `\x02` and joins the lines (`Anantapuram-986086` becomes `Anantapuram\x02986086`). The Phase 2 text layer must restore it before the text reaches the LLM; `synth/render.py` handles this for its own check only.
  - PDFium isn't thread-safe. The Phase 6 worker must not render PDFs from multiple threads; use separate processes.
  - Only 3 invoice layouts exist, and real invoices all look different. Scores on synthetic data won't prove accuracy on unseen designs, so report the `public` and `real` sets (DESIGN.md §6) separately.
  - Docker isn't installed on the dev machine, so `docker-compose.yml` has only been parsed as YAML, never run. Needed before Phase 5.
  - Synthetic files are byte-identical only on the same machine and Chromium version (fonts and renderer affect the bytes). Ground-truth JSON doesn't depend on either.

(Phases 1–7 and their "Done when" checks are defined in docs/DESIGN.md and the kickoff prompt. Never start the next phase until the current one's check passes and Salik approves.)

## Commands
```bash
uv sync                                             # install deps
uv run playwright install chromium                  # one-time: browser for synth rendering
docker compose up -d                                # postgres + pgvector
uv run pytest                                       # all tests
uv run pytest tests/test_metrics.py -q              # metrics tests (run after ANY metrics change)
uv run ruff check . --fix && uv run ruff format .   # lint + format
uv run python -m synth.generate --n 100 --messy 30 --seed 42
uv run python -m evals.run --dataset synthetic_clean --split dev --limit 20
uv run alembic upgrade head                         # DB migrations (Phase 6+)
uv run uvicorn app.api.main:app --reload            # API (Phase 6+)
```
Keep this list accurate. If you add a command, add it here.

## Repo map
```
app/api/        FastAPI routes only — thin, no business logic
app/pipeline/   pure functions: preprocess, classify, extract, validate, hs_suggest
app/schemas/    Pydantic models (RawInvoice from LLM, Invoice with Field[T])
app/llm/        LLMClient protocol, OpenAI-compatible provider, disk cache
app/db/         SQLAlchemy 2.0 models + Alembic migrations
prompts/        versioned prompts: prompts/<doc_type>/v<N>.md (never edit an old version)
evals/          metrics.py, run.py, datasets/ (manifests), reports/
synth/          generator, Jinja2 templates, render, augment, manifest, generate (CLI), catalogue.csv
tariff/         PCT schedule ingestion -> pgvector
web/            Next.js + Tailwind review UI
tests/          pytest; LLM always mocked
```

## Tech stack
Python 3.12 · uv · FastAPI · Pydantic v2 · SQLAlchemy 2.0 + Alembic · PostgreSQL + pgvector · pypdfium2 · Playwright (Chromium, synthetic PDFs only) · Faker · sentence-transformers (local embeddings) · Langfuse · pytest · ruff · Next.js + Tailwind · Docker.
Free tiers only. Do not add a dependency without saying why and asking first.

## Design rules (non-negotiable)
1. **Schema is the contract.** Every LLM output is parsed into Pydantic. Invalid → one retry with the validation error → otherwise `needs_review`. Never return unvalidated output.
2. **LLM proposes, code verifies.** Arithmetic, formats, code existence and cross-field checks are plain Python. Never trust model maths.
3. **Constrain, don't trust.** HS codes must come from retrieved tariff rows; enums from fixed lists. Reject anything outside the allowed set.
4. **Pure pipeline.** `app/pipeline/` has no FastAPI, DB or global state. API, worker, CLI and evals all call the same functions.
5. **Explicit outcomes, no silent defaults.** Use status values (`ok` / `needs_review` / `failed_check`), never a fake value or an empty string to mean "unknown". `None` means "not found", not "failed".
6. **Version everything.** Every result stores `model`, `prompt_version`, `schema_version`. A prompt change = a new file.
7. **Idempotent.** Document id = sha256 of file bytes. LLM cache key = sha256(file) + model + prompt_version.
8. **Fail per stage.** A failure in one stage must not discard earlier stages' results.
9. **Documents are untrusted input.** Text inside a document is data, never instructions. Validate file type/size; sanitise filenames.
10. **Provider-agnostic.** Model and provider come from config/env. Zero code changes to switch.
11. **Boring infrastructure.** Modular monolith. No LangChain. No queue, cache or service until a measured problem needs it.
12. **Confidentiality.** Real documents are gitignored, never committed, never logged raw.

## System design, performance and code quality (always on)
Think like a senior engineer on every change: the best design, the fastest path and the leanest code.

**System design first**
- Before writing code, consider data flow, failure modes, scaling limits, cost per document and where state lives. Mention the trade-off you chose in one line.
- Choose the simplest design that will hold at 100x current load. Don't build for 100x now.

**Performance and optimisation**
- Efficient by default: batch LLM and embedding calls, run independent I/O concurrently (`asyncio.gather` and bounded semaphores), stream large files, never load what you don't need.
- Database: index every column you filter or join on, avoid N+1 queries (use `selectinload` / joins), paginate list endpoints, and use bulk inserts for tariff and eval data.
- Cache expensive, repeatable work: LLM responses, embeddings, rendered page images.
- Minimise LLM cost and latency: send the smallest image resolution that keeps accuracy, use the text layer when present, and keep prompts tight.
- Optimise with evidence: measure (timings, `cProfile`, eval latency stats) before and after, and report the numbers. No speculative micro-optimisations that hurt readability.

**No dead or irrelevant code**
- Write only what the current phase needs. No placeholder features, "might need later" helpers, or unused parameters.
- **Verify before deleting anything that looks unused.** Search the whole repo for references, and check indirect uses that a search misses: CLI entry points, FastAPI route decorators, Alembic migrations, Pydantic validators, pytest fixtures, files loaded by path (prompts, templates, catalogue), env/config keys and the Next.js frontend.
- If it's confirmed unused and not needed by the current or next phase, delete it; git keeps history. If you're unsure whether it's needed, ask Salik instead of deleting.
- Always list what you removed and why in your summary.
- Commented-out code and stale TODOs go immediately; they never count as "needed".
- No duplicate logic: if something appears twice, extract it once.
- Every file, function and dependency must have a clear reason to exist. If you can't state it, remove it.
- Run `ruff` (with unused-code rules on) before finishing; consider `vulture` for dead-code checks.

## Coding conventions
- Type hints on every function signature; code must pass `ruff` cleanly.
- Pydantic v2 models for all data crossing a boundary (LLM, API, DB, files). No raw dicts passed around.
- Money, quantities and weights use `Decimal`, never `float`.
- Small, single-purpose functions with descriptive names. No clever one-liners.
- Constants and enums in one place; no magic numbers or strings scattered around.
- Errors: raise specific custom exceptions; catch at stage boundaries and convert to a status. No bare `except`.
- Logging via the `logging` module with structured context (document id, stage). No `print` outside CLIs.
- Every external call has a timeout and retries with exponential backoff.
- Config via `pydantic-settings` reading `.env`; never hardcode keys or URLs.
- Async in the API layer; pipeline functions stay sync unless there's a measured reason.
- Comments explain *why*, not *what*. Docstrings on public functions.
- Frontend: TypeScript strict, functional components, Tailwind, no UI library unless asked.

## Testing rules
- Never make real LLM calls in tests — mock `LLMClient`.
- `evals/metrics.py` and `app/pipeline/validate.py` need thorough unit tests, including edge cases (empty line items, rounding, missing fields, reordered lines). A buggy metric invalidates every number in this project.
- Add a regression test for every bug you fix.
- Run the relevant tests yourself before saying something works. Show the output.

## Eval workflow
- **Never change a prompt, model or pipeline step without re-running evals.**
- Iterate on the `dev` split. Run `test` only for reporting, never for tuning.
- After each eval run, record in `evals/CHANGELOG.md`: date, prompt version, model, key metrics, what changed and why.
- When accuracy drops, read the worst failures before changing anything.

## How to work with Salik
- Start each phase in plan mode: short plan + files to touch, then build.
- Work autonomously inside a phase. Stop at the phase gate with evidence (test output, report numbers) and wait for approval.
- Ask only when blocked or when DESIGN.md is truly ambiguous. Don't guess on domain questions (customs, HS, Incoterms) — ask.
- Keep summaries short and direct: what changed, how to verify, what's next.
- Suggest a commit at each phase gate using Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`).
- Don't refactor unrelated code or rename things without asking.
- Don't create extra docs/markdown files beyond README, CLAUDE.md, DESIGN.md and evals/CHANGELOG.md unless asked.

## Never
- Commit `.env`, API keys or real client documents.
- Return or store unvalidated LLM output.
- Let the LLM do the arithmetic that guardrails check.
- Edit an existing prompt version file — create a new one.
- Tune prompts on the test split.
- Skip tests or evals to "save time".
