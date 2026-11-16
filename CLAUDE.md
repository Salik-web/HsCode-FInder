# CLAUDE.md — TradeDoc AI

Read this file and `docs/DESIGN.md` at the start of every session. Update the **Status** section at the end of every phase.

## What this project is
TradeDoc AI extracts structured data from trade documents (commercial invoices first, then packing lists and bills of lading) using vision LLMs, validates it with deterministic guardrails, suggests Pakistan Customs Tariff (HS/PCT) codes via RAG, and measures everything with an eval harness.

**The point of this project is evals and guardrails, not the UI.** Every change is judged by eval numbers.

Built by Salik (Full Stack AI Engineer) for his freight forwarding business (H & J Corporation) and as a public portfolio project.

## Status
- Current phase: **Phase 3 started early (schema reliability)**, approved by Salik on 2026-11-11. Phase 2 is built and tested, and its baseline was cut short by decision (see below); the Phase 2 gate wasn't formally passed.
- Completed phases: Phase 1 — Foundation + synthetic data (approved by Salik, 2026-11-07)
- Branch: everything is merged into `main`; no open feature branches. Salik pushes.
- **Phase 4 started 2026-11-16** at Salik's request while Groq quota recovers, before the Phase 3 gate is formally closed (the full v3 run is still finishing). Built: `app/pipeline/validate.py` (deterministic checks), exact code synonyms, the injected-error set and the guardrail eval. Catch rate 60/60 = 100% on the injected set; 0 false alarms on 113 clean ground-truth invoices; 0 fully correct v3 extractions flagged. See evals/CHANGELOG.md. **Next session, after tomorrow's runs: confidence scoring and the calibration table** (the rest of the Phase 4 gate).
- Latest eval baseline: **v2 on Groq** `qwen/qwen3.8-27b`, thinking off, 2026-11-11: 18 documents (the first 16 clean dev and the first 2 messy dev by id, all seen layouts). Clean: critical-field accuracy 85.1%, all critical right 14/16, first-try valid JSON 8/16, `needs_review` 2/16; messy: 2/2 valid first try, all critical right 1/2. See evals/CHANGELOG.md.
- Latest result on those 18 documents: **prompt v3 + deterministic fixes** (key repair, reference-buyer rule, unit list, number normalisation), re-scored from cache on 2026-11-12. Clean: first-try valid 16/16, critical-field accuracy 100%, all fields 1088/1090 (the 2 misses are garbled units, both flagged `failed_check`). Messy: first-try valid 2/2, all critical right 1/2 (incoterm misread on a blurred photo). See evals/CHANGELOG.md.
- **Where we stopped (2026-11-16, 07:05 UTC): the full v3 run, approved by Salik, is part-way through.** test_unseen_layout is done and the interim report was sent to Salik. Groq's daily token limit then stopped dev.
  - Critical fields now include `buyer.name` and `consignee.name` (Salik, 2026-11-16); every number below uses that definition.
  - test_unseen_layout clean: **24/24, final report in `evals/reports`** (`20261116T074314_…`): critical 99.1%, all critical right 22/24. The errors are `buyer.name` and `consignee.name` on `ledger` (one document) and one missed line (another).
  - test_unseen_layout messy: **7/7 live**, but no final report yet. One document's first attempt was a Groq `json_validate_failed` rejection, which the old cache didn't store, so `--offline` scores 6/7 (critical 98.5%, 5/6 right; `consignee.name` on `ledger`). The cache now stores rejections. Re-run the live command once (one call), then write the final report with `--offline`.
  - dev clean: **27/53 cached**, all critical right; 26 to go. dev messy: 2/16 (1/2 right, incoterm); 14 to go.
  - **Ledger diagnostic batch** (Salik, 2026-11-16): 10 clean `ledger` invoices, seed 1116, in `data/diagnostic_ledger` (split `diagnostic`, ids `diag_*`, outside every split), generated and not run yet. Run it live, then show Salik exactly what goes wrong with party names and why. **Don't fix anything until Salik has seen the diagnosis.**
    - What we know before running: `ledger` is the only layout that prints a party as one line, `name, address, country`. About 25% of generated company names contain commas. In this batch, `diag_00000` (shipper and consignee), `diag_00001` (shipper) and `diag_00009` (consignee) have comma names; the other 7 are the control group. On the test split, ledger addresses were 100% right while names failed, so the likely error is a name cut at a comma, not a shifted name/address boundary. Check that against the batch; it's a hypothesis.
    - **Holdout decision (Salik, 2026-11-16), applies only if the diagnosis leads to a fix, and only after Salik has seen the diagnosis:**
      1. Freeze the current v3 test_unseen_layout results as recorded in evals/CHANGELOG.md.
      2. Move `ledger` into the seen group from then on.
      3. Add a new held-out layout that also prints a party as one line (name and address joined) but looks different from `ledger`.
      4. The fix must be general (how to find where a party name ends), never specific to `ledger`.
  - No processes were left after the run (checked with `Get-CimInstance Win32_Process`).
  - **Next session, in this order (Salik, 2026-11-16):** 1) unseen messy (1 call); 2) the ledger diagnostic batch (about 10 calls), then the diagnosis to Salik; 3) dev messy (14); 4) dev clean last (26). About 51 calls, about 1.7 days of quota, so dev clean will probably finish the day after.
- **Resume the full run.** Each command stops at the first quota error and resumes from the cache next time, so repeat it until it prints the full count. Keep interim reports out of the repo with `REPORTS_DIR`:
  ```bash
  export REPORTS_DIR=/tmp/interim_reports   # any folder outside evals/reports
  uv run python -m evals.run --dataset synthetic_messy --split test_unseen_layout --prompt v3   # until Scored 7/7
  uv run python -m evals.run --dataset synthetic_clean --split diagnostic --folder diagnostic_ledger --prompt v3   # until Scored 10/10
  # -> ledger diagnosis to Salik (the report lists each document's wrong fields)
  uv run python -m evals.run --dataset synthetic_messy --split dev --prompt v3                  # until Scored 16/16
  uv run python -m evals.run --dataset synthetic_clean --split dev --prompt v3                  # until Scored 53/53
  ```
  When a split is fully scored, write its final report into `evals/reports` from the cache at no cost (`unset REPORTS_DIR`, then the same command with `--offline`), and log it in `evals/CHANGELOG.md`. Groq allows about 200,000 tokens a day, but counts about 6,300 a call, so about 30 v3 calls on a full budget (see Known issues). If you run the commands in a background loop, check afterwards that no `bash`, `sleep` or `evals.run` process is left (see the lesson below).
  - Lesson: on Windows, stopping a background shell loop ended only its wrapper. Two loops kept running overnight on 2026-11-11 (5 extra v2 calls; one v3 retry made twice). After stopping a background loop, check with `Get-CimInstance Win32_Process` and kill leftovers by PID.
  - To replay the v2 baseline from cache, set `LLM_RESPONSE_FORMAT=json_schema` with `--prompt v2`: JSON object mode changed the cache key.
- Known issues / open decisions:
  - **Charges and discounts, prompt v4 (planned by Salik, 2026-11-16, after tomorrow's runs):** real invoices often show freight, insurance, discounts and other charges between the subtotal and the total. v4 extracts them as fields, and the total check becomes lines + charges − discount = total with a rounding-only tolerance. A mismatch with no charges found goes to `needs_review`. The generator must produce invoices with charges too. Until then, the total check (rounding only, half a cent per line plus half for the total) flags the total on any real invoice with charges.
  - **Code synonyms (Salik, 2026-11-16):** exact only, mapped before validation, with the printed value recorded in `rules_applied`: C&F and CNF -> CFR, RMB -> CNY, US$ -> USD (`app/pipeline/rules.py`). DDU stays flagged (not the same rule as DAP), and so does Rs (ambiguous across rupee currencies).
  - Model is Groq `qwen/qwen3.8-27b` with thinking off (`reasoning_effort=none`), chosen by Salik to stay on the free tier (2026-11-11). Gemini's free tier allows only 20 requests per model per day; its 12 cached results are kept for the Phase 7 bake-off (`LLM_PROVIDER=gemini LLM_MODEL=gemini-3.5-flash LLM_REASONING_EFFORT= LLM_RESPONSE_FORMAT=json_schema LLM_MAX_OUTPUT_TOKENS=16000`, prompt v1).
  - **Units:** the accepted list is PCS, NOS, SET, PAIR, CTN, BAG, KG, MTR, MT (`app/schemas/constants.py`) plus exact synonyms in `app/pipeline/rules.py`. NOS is its own unit, never mapped to PCS; TON and M stay failing as ambiguous (Salik, 2026-11-12). **Align the list with Pakistan Customs/WeBOC unit codes once Salik confirms them with his operations contacts.**
  - Number formats: when both "," and "." appear, the last one is the decimal mark (confirmed by Salik, 2026-11-12). A single separator with exactly 3 digits after it is settled only by the document's other numbers; otherwise the document goes to `needs_review`. Currency is never used as evidence.
  - Groq per-minute limits are respected by client-side throttling (`app/llm/throttle.py`, `LLM_TOKENS_PER_MINUTE`, `LLM_OUTPUT_TOKENS_PER_MINUTE`, `LLM_REQUESTS_PER_MINUTE`), not by retries.
  - **Line count vs output limits:** Groq's free tier caps `qwen/qwen3.8-27b` at 1,000 output tokens a minute, which is also a per-call ceiling. Prompt v2 writes line items as fixed-order arrays (`app/schemas/compact.py`): about 34–37 output tokens a line plus about 270 for the header and parties, so roughly 19–21 lines fit on a typical invoice and 16–18 in the worst case. Every model has some output limit, so long real invoices will exceed it eventually. **Plan chunked line-item extraction in Phase 3** (header in one call, line items in page or row ranges).
  - **Fallback gap:** `qwen/qwen3.6-27b` isn't served on Groq, and `qwen/qwen3.8-27b` is the only Groq model that accepts images, so the configured fallback is the same model and gives no protection. Phase 6 should use Gemini as the cross-provider fallback (`FALLBACK_PROVIDER=gemini`). `LLM_MAX_OUTPUT_TOKENS` is shared by both roles today and Gemini needs about 16000 for thinking, so make the cap per role then. Evals run without fallback either way, so one run measures one model.
  - Groq's free tier also allows only 200,000 tokens a day for `qwen/qwen3.8-27b`. It refills continuously at about 139 tokens a minute (200,000 / 1,440), not at a daily reset: on 2026-11-16 a 429 said 988 tokens were short and to retry in 7 minutes. Groq also counts more than the responses' `usage` shows: 31 calls on 2026-11-16 used 121k tokens by `usage` (135k with the 1,000-token output reservation), but Groq reported 196k used. The cause is unknown (image tokens left out of `usage` is one guess). Plan on about 6,300 Groq-counted tokens a call, so about 30 calls on a full budget. A document costs about 4,000 tokens a call with a json_schema, about 3,860 per clean document with prompt v3 in JSON object mode (3,375 in, 484 out; measured 2026-11-12), and a retry doubles it.
  - Schema reliability on Groq: in the v2 baseline only 50% of clean documents were valid on the first try (misspelt keys like `shiftper`, invented keys like `document_type`), and 2 failed twice. Phase 3 work in progress: key repair, prompt v3, JSON object mode (see Where we stopped).
  - **Latency:** reports split model call time (final HTTP attempt) from retry backoff and throttle waits, and the p95 target applies to model call time only (Salik, 2026-11-16). Calls cached before 2026-11-16 recorded only call + backoff, so they show "not recorded". Model call figures appear as the remaining calls run (dev clean, dev messy, the unseen messy re-call).
  - Reports show the first-try valid JSON rate and the retry rate per layout group, with counts. With provider strict mode off, they are how we watch schema reliability.
  - Groq's strict JSON-schema mode is off (`LLM_STRICT_SCHEMA=false`): it often returned an empty or truncated object (keys in alphabetical order, closed after two). Without it, Pydantic validation plus one retry enforces the schema. Groq's own schema rejections (400 `json_validate_failed`) still go through that retry, and "Request too large" fails only that document.
  - `LLM_MAX_OUTPUT_TOKENS` defaults to 1000 for Groq. Thinking models (the Gemini replay) need `LLM_MAX_OUTPUT_TOKENS=16000`.
  - Langfuse keys aren't in `.env`, so tracing is built and unit-tested but has never sent a real trace.
  - `gemini-3.7-flash` and `gemini-3.8-flash` hang on this key; `gemini-3.6-flash` returned 503. The 429 quota record names `gemini-3.6-flash` while responses say `gemini-3.5-flash`, so Google may be serving 3.5 from 3.6.
  - About 30% of buyers who are the consignee print "Same as consignee" (or a synonym). Ground truth holds the full buyer; reports score these as the `reference` slice.
  - `synth/catalogue.csv` holds 30 unverified placeholder rows: HS codes, units and `kg_per_unit` are all estimates. Replace with real PCT entries.
  - Unit prices are random, not tied to the goods. Accepted for now.
  - pypdfium2 has only been tested on our own Chromium PDFs. Verify it on scanned and real documents in Phase 3.
  - PDFium mangles hyphens at a line wrap in two ways. After a letter it substitutes `\x02` and joins the lines (`Anantapuram-986086` becomes `Anantapuram\x02986086`). After a digit it keeps the hyphen and the line break (`4-3` becomes `4-` then `3`). This is observed behaviour, not documented. The Phase 2 text layer must clean both before the text reaches the LLM; `synth/render.py` handles this for its own check only.
  - A PDF's text layer can be wrong even when the page looks right. Cambria left stray characters in one PDF: PDFium read `İçtaş İnşaat` as `İçİ taş İnİ şaat`, while PyMuPDF read it correctly. So `summary` uses Calibri, and Phase 2 should treat the text layer as a hint and the page image as the source of truth.
  - PDFium isn't thread-safe. The Phase 6 worker must not render PDFs from multiple threads; use separate processes.
  - Synthetic layouts are still only 8 designs by one author, and `test_unseen_layout` shares their fonts, label lists and catalogue. It measures layout generalisation, not real-world accuracy, so report the `public` and `real` sets (DESIGN.md §6) separately.
  - Messy images are single-page only, so two-page invoices are tested in clean form only.
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
uv run python -m synth.generate --diagnostic ledger --n 10 --seed 1116   # one layout, own folder, outside every split
uv run python -m evals.run --dataset synthetic_clean --split diagnostic --folder diagnostic_ledger --prompt v3
uv run python -m synth.inject --n 60 --seed 2026                     # injected-error set, own folder and split
uv run python -m evals.guardrails                                    # catch rate + false alarms, no LLM calls
uv run python -m evals.run --dataset synthetic_clean --split dev --limit 20
uv run python -m evals.run --dataset synthetic_messy --split dev --model gemini-3.5-flash --prompt v1
uv run python -m evals.run --dataset synthetic_clean --split test_unseen_layout   # reporting only
uv run python -m evals.run --dataset synthetic_clean --split dev --fallback       # let the fallback model answer on rate limits
uv run alembic upgrade head                         # DB migrations (Phase 6+)
uv run uvicorn app.api.main:app --reload            # API (Phase 6+)
```
Keep this list accurate. If you add a command, add it here.

## Repo map
```
app/api/        FastAPI routes only — thin, no business logic
app/pipeline/   pure functions: preprocess, classify, extract, validate, hs_suggest
app/schemas/    Pydantic models (RawInvoice from LLM, Invoice with Field[T])
app/llm/        LLMClient protocol, OpenAI-compatible provider, disk cache, fallback, Langfuse tracing, factory
app/db/         SQLAlchemy 2.0 models + Alembic migrations
prompts/        versioned prompts: prompts/<doc_type>/v<N>.md (never edit an old version)
evals/          metrics.py, dataset.py, report.py, run.py (CLI), guardrails.py (CLI), reports/, CHANGELOG.md
synth/          generator, layouts (+ label synonyms), Jinja2 templates, render, augment, manifest, generate (CLI), inject (CLI), catalogue.csv
tariff/         PCT schedule ingestion -> pgvector
web/            Next.js + Tailwind review UI
tests/          pytest; LLM always mocked (tests/fakes.py)
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
- Iterate on the `dev` split. Run `test` and `test_unseen_layout` only for reporting, never for tuning.
- Every eval report shows accuracy separately for seen and unseen layouts, using `layout_group` in the manifest. Never report one blended number.
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
- Tune prompts on the test or test_unseen_layout splits, or on the held-out layouts' templates.
- Skip tests or evals to "save time".
