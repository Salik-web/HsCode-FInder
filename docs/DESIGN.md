# TradeDoc AI — Project Design Doc

Nov 7, 2026 · Syed Salik Alvi

## 1. Overview

**Problem.** A freight forwarder or clearing agent re-types data from commercial invoices, packing lists and bills of lading into quotes, customs filings and client updates. It's slow, error-prone, and HS classification depends on someone experienced being available.

**What TradeDoc AI does.** You upload a trade document (a PDF or a phone photo). The system:

1. Detects the document type (invoice, packing list or bill of lading)
2. Extracts structured data (parties, line items, quantities, values, weights, Incoterms, currency, dates) as validated JSON
3. Runs deterministic checks (totals add up, weights are consistent, formats are valid)
4. Suggests the top-3 Pakistan Customs Tariff (PCT) codes per line item, with reasoning and confidence
5. Sends low-confidence or failed fields to a human review screen; corrections are logged
6. Exports the approved data as JSON or CSV

**Who it's for.** H & J Corporation's own operations first; it's also a public portfolio project showing evals, guardrails and LLM ops.

**Goals**

- Measured, reproducible accuracy for every field, published in the README
- No unvalidated LLM output ever reaches the user
- Runs at zero cost on free tiers

**Non-goals (v1)**

- Filing directly to customs systems (WeBOC)
- Handwritten or Urdu documents
- Multi-tenant SaaS, billing, or selling it

**Success metrics (targets, adjust after baseline)**

| Metric | Target |
| --- | --- |
| Critical-field accuracy (clean docs) | ≥ 95% |
| Critical-field accuracy (messy docs) | ≥ 85% |
| HS code top-3 accuracy (6-digit) | ≥ 80% |
| Injected-error catch rate (guardrails) | ≥ 95% |
| Cost per document | < $0.01 |
| p95 latency per document | < 30 s |

## 2. Tech stack

The rule here: reuse what you already know from Verdict, the Research Agent and the Text-to-SQL Agent, so the learning goes into evals and guardrails rather than new frameworks. Everything has a free option.

| Layer | Choice | Why |
| --- | --- | --- |
| Language | Python 3.12 + uv | Main AI language; uv is fast and reproducible |
| API | FastAPI | Async, auto OpenAPI docs |
| Schemas / validation | Pydantic v2 | The core of the project: every LLM output is parsed into a model |
| Structured LLM output | Native JSON-schema mode, or the `instructor` library | Validation with automatic retries |
| LLM access | OpenAI-compatible client behind your own `LLMClient` interface | Swap providers via config |
| Vision models | Free tiers (e.g. Gemini Flash via Google AI Studio, open vision models via OpenRouter or Groq). Check current free limits | Zero budget; the bake-off compares them |
| PDF / image handling | pypdfium2, Pillow | Renders pages, reads the text layer when one exists |
| Orchestration | Plain Python functions first; LangGraph in Phase 6 | LangGraph's interrupts fit the human-review step |
| Database | PostgreSQL + pgvector (SQLAlchemy 2 + Alembic) | One DB for app data and tariff vectors |
| Embeddings | `bge-small` / `e5-small` via sentence-transformers, run locally | Free and fast enough for ~10k tariff lines |
| Background jobs | FastAPI BackgroundTasks → arq or Celery + Redis later | Don't add a queue until you need one |
| Frontend | Next.js + Tailwind (or Streamlit for early demos) | Ships easily on Vercel |
| Observability | Langfuse (free cloud or self-hosted) | Traces, cost and prompt versions for every LLM call |
| Testing / CI | pytest + GitHub Actions | Runs the eval gate on every PR |
| Synthetic data | Faker, Jinja2 HTML templates, Playwright (headless Chromium), Pillow + numpy for noise | Generated documents come with ground truth for free |
| Deploy | Backend: Hugging Face Spaces (Docker). DB: Neon or Supabase free tier. Frontend: Vercel | All free |

**Deliberately left out:** Pinecone (pgvector is enough and keeps one DB), microservices, Kubernetes, and fine-tuning in v1.

## 3. System architecture

A modular monolith: one FastAPI app, one worker, one Postgres. Each pipeline stage is a pure function that the API, the worker and the eval harness all call, so what you evaluate is exactly what you ship.

```
[Next.js UI] --upload--> [FastAPI /documents]
                              |
                   hash file (SHA-256) -> already seen? return cached result
                              |
                   save file -> create job -> return job_id
                              |
                        [Worker]
   1. preprocess   : pypdfium2 -> page images (+ text layer if present)
   2. classify     : invoice | packing_list | bill_of_lading
   3. extract      : vision LLM -> JSON -> Pydantic (retry once with errors)
   4. validate     : deterministic business rules -> field status
   5. hs_suggest   : embed item desc -> pgvector top-10 -> LLM picks top-3
   6. persist      : extraction_run (+ model, prompt_version, cost, latency)
                              |
[Review UI] <--SSE/poll-- status=needs_review | ready
     | human edits/approves -> corrections table -> export JSON/CSV

[Eval harness] -- calls the same pipeline() on labelled datasets --> eval_runs
[Langfuse]     <-- traces from every LLM call
```

**Request flow rules**

- Upload is synchronous and fast: it only stores the file and queues a job.
- Processing is asynchronous; the UI polls `/jobs/{id}` or listens over SSE.
- Each stage writes its result and status, so a failure in stage 5 still leaves stages 1–4 usable.

## 4. Data model

**Extraction schema (Pydantic).** Every field carries a value, a confidence and a status.

```python
class Field(BaseModel, Generic[T]):
    value: T | None
    confidence: float  # 0-1, from model + rule checks
    source_page: int | None  # provenance
    status: Literal["ok", "needs_review", "failed_check"] = "ok"


class Party(BaseModel):
    name: Field[str]
    address: Field[str]
    country: Field[str]  # ISO-3166 alpha-2


class LineItem(BaseModel):
    description: Field[str]
    quantity: Field[Decimal]
    unit: Field[str]
    unit_price: Field[Decimal]
    amount: Field[Decimal]
    hs_code_on_doc: Field[str] | None
    hs_suggestions: list[HSSuggestion] = []


class Invoice(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    invoice_number: Field[str]
    invoice_date: Field[date]
    shipper: Party
    buyer: Party  # sold-to / bill-to
    consignee: Party  # ship-to / deliver-to
    notify_party: Party | None
    incoterm: Field[
        Literal["EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP"]
    ]
    currency: Field[str]  # ISO-4217
    line_items: list[LineItem]
    total_amount: Field[Decimal]
    gross_weight_kg: Field[Decimal] | None
    net_weight_kg: Field[Decimal] | None
```

Tip: let the LLM fill a simpler "raw" schema (plain values), then compute confidence and status in code. Don't let the model grade its own confidence alone.

**Parties are separate fields, never merged.**

| Field | Also labelled | Meaning |
| --- | --- | --- |
| `shipper` | Exporter, Consignor, Shipped By | Who ships the goods |
| `buyer` | Sold To, Bill To, Purchaser | Who buys and pays |
| `consignee` | Ship To, Deliver To, Consigned To | Who receives the goods |
| `notify_party` | Notify, Also Notify | Who is told of arrival, often the clearing agent; `None` when absent |

Buyer and consignee are often the same company, but not always. When they're the same, both fields hold the same values. "Seller", "Supplier" and "Importer" are deliberately unused in synthetic data, because on real documents they can each refer to more than one party.

**Database tables**

| Table | Key columns |
| --- | --- |
| documents | id (sha256), filename, mime, pages, doc_type, status, created_at |
| extraction_runs | id, document_id, model, prompt_version, schema_version, output (jsonb), cost_usd, latency_ms, created_at |
| hs_suggestions | run_id, line_index, candidates (jsonb), chosen_code |
| corrections | id, run_id, field_path, old_value, new_value, created_at |
| tariff_codes | code, description, chapter, heading, embedding vector(384) |
| eval_runs | id, git_sha, dataset, model, prompt_version, metrics (jsonb), created_at |

## 5. Pipeline components

**5.1 Ingest and preprocess**

- Accept PDF, JPG and PNG; reject anything else; cap at 10 MB and 5 pages.
- Render each page to an image at ~150 DPI with pypdfium2. If the PDF has a text layer, send that text too (it improves accuracy and is cheap).
- For photos: auto-rotate (EXIF), deskew, and downscale to the model's maximum resolution.

**5.2 Classify**

- One cheap LLM call, or rules on keywords ("Bill of Lading", "Packing List"), returns the document type plus a confidence.
- The type selects which schema and prompt to use.

**5.3 Extract**

- Prompt = system instructions + JSON schema + page images (+ text layer). Prompts live in `prompts/<doc_type>/v<N>.md`.
- Parse into Pydantic. On a validation error, retry once with the error message appended. If it still fails, mark the document `needs_review`; never guess.
- Treat document text as untrusted data: an invoice that says "ignore previous instructions" is just text to extract.

**5.4 Validate (guardrails, all deterministic)**

- Each line's `quantity × unit_price ≈ amount` (± 0.01)
- Sum of line amounts ≈ total (± 0.5%)
- `net_weight ≤ gross_weight`
- Incoterm is in the allowed list; currency is a valid ISO-4217 code; country is a valid ISO code
- Date parses and isn't in the future or more than two years old
- HS code on the document (if any) exists in the tariff
- A failed check sets that field's status to `failed_check` and lowers its confidence

**5.5 HS code suggestion (RAG + rerank)**

- Offline: parse the PCT schedule into rows (code, description, chapter, heading), embed the descriptions, and store them in pgvector.
- Online: embed the line-item description, retrieve the top 10 (optionally filtered by a chapter predicted first), and have the LLM choose the top 3 with a one-line reason each. Every chosen code must be among the retrieved candidates; reject anything else, because that's a hallucination.
- Always label these as suggestions. A licensed person makes the final classification.

**5.6 Review and export**

- The UI shows the document image next to the fields, with `needs_review` and `failed_check` highlighted.
- Every edit is written to `corrections`. That becomes new eval data and tells you which fields are weakest.
- Export only after approval.

## 6. Evaluation strategy

This is the heart of the project. Build it before you optimise anything.

**Datasets**

| Set | Size | Purpose |
| --- | --- | --- |
| synthetic_clean | 100 | Baseline accuracy on generated PDFs |
| synthetic_messy | 30–50 | Robustness: photographed, blurred, rotated, compressed |
| synthetic_injected | 30 | Docs with deliberate errors (wrong total, net > gross) to measure guardrail catch rate |
| public | 30–50 | Real-world layouts from public invoice/form datasets (SROIE, CORD, FUNSD or HF invoice sets) |
| hs_gold | 50–100 items | Product descriptions with known PCT codes |
| real | grows over time | Redacted real documents from H & J shipments, plus corrections |

Split each set into **dev (70%)** for prompt iteration and **test (30%)** that you only run for reporting, so you don't overfit prompts to the test set.

Synthetic sets have 8 layouts and vary their field labels per document. In synthetic data the buyer is the consignee about 60% of the time, and about 40% of invoices name a notify party. Two layouts (`sidebar`, `ledger`) are held out entirely in a third split, **test_unseen_layout**. They never appear in dev or test, and only the remaining 6 layouts split 70/30. Like test, test_unseen_layout is for reporting only.

**Metrics**

- Per-field accuracy: exact match for IDs and codes, ± tolerance for numbers, normalised fuzzy match (≥ 0.9) for names and addresses
- Line items: match predicted to gold lines (Hungarian matching on description similarity), then precision, recall and F1, plus per-field accuracy on matched lines
- Document-level: % of documents with **all critical fields** correct (invoice number, total, currency, Incoterm, line amounts)
- Guardrail catch rate on `synthetic_injected`, and false-alarm rate on clean docs
- HS: top-1 and top-3 accuracy at 4-digit heading and 6-digit levels
- Calibration: accuracy within each confidence bucket. Fields marked "ok" with high confidence should almost always be right
- Cost per document, p50/p95 latency, failure/retry rate
- Seen vs unseen layouts: every eval report shows accuracy separately for each group, never one blended number. The gap between them shows how much a score depends on knowing the layout

**Harness**

- `python -m evals.run --dataset synthetic_clean --split test --model gemini-flash --prompt v3`
- It writes a row to `eval_runs` plus a markdown report with a diff against the previous run and the 10 worst failures.
- Cache LLM responses by (file hash, model, prompt version) so re-scoring is free.

**Regression gate (CI)**

- On every PR, run a 20-document smoke subset.
- Fail the build if critical-field accuracy drops by more than 2 points or any guardrail test fails.

## 7. System design rules

These are non-negotiable. Put them in the README so reviewers see the engineering thinking.

**Correctness**

1. **Evals before features.** No prompt, model or pipeline change merges without an eval run. The numbers decide, not vibes.
2. **The schema is the contract.** Every LLM output is parsed into Pydantic. Invalid output gets one retry with the error, then `needs_review`. Unvalidated output never reaches the user.
3. **The LLM proposes, code verifies.** Arithmetic, formats, code existence and cross-field consistency are checked in plain Python. The model never does maths that matters.
4. **Constrain, don't trust.** HS suggestions must come from retrieved tariff rows, and enums must come from fixed lists. Anything outside the allowed set is rejected.
5. **Confidence and provenance on every field.** Each value records where it came from (page) and how sure the system is, and confidence is calibrated against eval data.
6. **A human is the final authority.** Nothing is filed or sent automatically. Every correction is logged and becomes training and eval data.

**Reliability**

7. **Idempotent, content-addressed processing.** The document ID is the SHA-256 of the file. Re-uploads return the cached result. LLM calls are cached by (file hash, model, prompt version).
8. **Version everything.** Prompts live in files with version numbers. Every run stores model, prompt_version and schema_version, so any result can be reproduced.
9. **Fail per stage, not per document.** Each stage stores its own status and error. A failed HS step doesn't throw away a good extraction.
10. **Timeouts, retries and backoff on every external call**, plus a fallback model when the primary is rate-limited.
11. **Slow work is asynchronous.** The API only accepts uploads and queues jobs. Workers do the heavy lifting.

**Architecture**

12. **Pure pipeline functions.** `pipeline(file_bytes, config) -> Result` has no web or DB code inside, so the API, worker, CLI and eval harness all run the same code.
13. **Provider abstraction.** One `LLMClient` interface. The model is config, not code, and switching providers takes zero code changes.
14. **Boring infrastructure.** One repo, a modular monolith, one Postgres for app data and vectors. Add a queue, cache or service only when a measured problem demands it.
15. **Stateless API.** State lives in Postgres and files live in object storage (local disk in dev), so the API can restart or scale freely.

**Security and cost**

16. **Documents are untrusted input.** Text inside a document is data, never instructions. Validate file type and size, and sanitise filenames.
17. **Respect client confidentiality.** Redact real documents before adding them to eval sets, never commit them to a public repo, and don't log raw document content.
18. **Budgets.** Set a per-document page cap and a cost ceiling, and track tokens and cost per call in Langfuse.
19. **Observability from day one.** Every LLM call is traced with input size, output, latency, cost and prompt version.

## 8. Repo structure

```
tradedoc-ai/
├── app/
│   ├── api/            # FastAPI routes: documents, jobs, review, export
│   ├── pipeline/       # pure functions: preprocess, classify, extract, validate, hs_suggest
│   ├── schemas/        # Pydantic models (invoice, packing_list, bol)
│   ├── llm/            # LLMClient interface + providers + cache
│   ├── db/             # SQLAlchemy models, Alembic migrations
│   └── worker.py
├── prompts/
│   └── invoice/v1.md, v2.md ...
├── evals/
│   ├── datasets/       # manifests only; real docs gitignored
│   ├── metrics.py      # field matching, line-item matching, calibration
│   ├── run.py          # CLI harness
│   └── reports/
├── synth/
│   ├── generator.py    # Faker -> ground-truth JSON
│   ├── layouts.py      # layout list, held-out layouts, label synonyms
│   ├── templates/      # Jinja2 HTML invoice/packing list layouts
│   ├── render.py       # Playwright (Chromium) -> PDF
│   ├── augment.py      # blur, rotate, skew, noise, JPEG artefacts
│   ├── manifest.py     # manifest models, dev/test split
│   └── generate.py     # CLI entry point
├── tariff/
│   └── ingest.py       # parse PCT schedule -> embed -> pgvector
├── web/                # Next.js frontend
├── tests/
├── docker-compose.yml  # postgres+pgvector (+ redis later)
├── .github/workflows/eval-gate.yml
└── README.md           # problem, architecture, eval results table, design rules
```

## 9. Everything the project needs

**Accounts (all free)**

- [ ] GitHub repo (public, with real documents gitignored)
- [ ] API key for at least two vision-model providers (e.g. Google AI Studio, OpenRouter, Groq)
- [ ] Langfuse (cloud free tier, or self-host in Docker)
- [ ] Neon or Supabase (hosted Postgres with pgvector) for deployment
- [ ] Hugging Face Spaces (backend) and Vercel (frontend)

**Data**

- [ ] 3–4 commercial invoice and packing list layouts (from free templates)
- [ ] A product catalogue of ~200 items **taken from the tariff itself**, so every synthetic line item has a known correct HS code
- [ ] The Pakistan Customs Tariff schedule (from the FBR website)
- [ ] One or two public invoice/form datasets for real-world layouts
- [ ] Later: redacted real documents from H & J Corporation shipments

**Local tooling**

- [ ] Python 3.12, uv, Docker (Postgres + pgvector), Node.js for Next.js
- [ ] Chromium for PDF rendering (`uv run playwright install chromium`)

**Domain knowledge (ask experienced contacts)**

- [ ] Which fields on an invoice or packing list matter most for clearance and quotes
- [ ] How HS classification works in practice: the General Rules of Interpretation, common mistakes, and Pakistan-specific subheadings
- [ ] Which Incoterms and currencies come up most often in Pakistan trade

## 10. How to start

1. **Foundations.** Create the repo and run `uv init`. Write the README's problem statement and the design rules. Write the Pydantic `Invoice` schema. Bring up `docker-compose` with Postgres + pgvector.
2. **Product catalogue and generator.** Pull ~200 product descriptions straight from the tariff schedule, each with its code. Write `synth/generator.py`: random parties (Faker), 1–15 line items from the catalogue, Incoterm, currency and weights, with computed totals. Save the ground-truth JSON.
3. **Render and mess up.** Write 3 Jinja2 HTML layouts, render 100 PDFs with Playwright, then run `augment.py` to make 30 messy image versions. Inspect 10 by eye.
4. **Extraction v0.** Write the `LLMClient` interface and one provider. Implement `extract(file) -> Invoice` with structured output and one retry. Run it on 5 documents by hand.
5. **Eval harness v0.** Write field-level matching and the line-item matching. `evals/run.py` prints a per-field accuracy table and the worst failures.
6. **Baseline and first iteration.** Run on the dev split, read every failure, and group them into categories. Fix the top category (prompt, preprocessing or schema). Re-run and record the before/after numbers.
7. **Ship it publicly.** Push the code and post the baseline numbers and what you changed. Building in public starts here.

## 11. Roadmap (phases — move to the next as soon as "Done when" passes)

| Phase | Milestone | Done when |
| --- | --- | --- |
| 1 | Foundation + synthetic data | 130 docs generated, generator tests pass, samples reviewed |
| 2 | Extraction + eval harness + tracing | Metrics unit-tested; baseline report for clean and messy dev sets |
| 3 | Iterate to quality | Clean critical-field accuracy ≥ 90% |
| 4 | Guardrails + injected-error dataset + confidence scoring | Catch rate ≥ 95%; calibration table in the report |
| 5 | Tariff ingestion, pgvector, HS suggestion | HS top-3 measured on hs_gold |
| 6 | FastAPI + worker + review UI; corrections logging | Upload → review → export works end to end locally |
| 7 | Model bake-off, CI eval gate, deploy, README | Live demo URL; README results table |
| Later | Packing lists, bills of lading, real-document eval set, WhatsApp status bot, quote calculator | — |

## 12. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Synthetic docs are too easy, so the numbers flatter you | Messy augmentation, public real-world datasets, then real redacted docs; report each set separately |
| HS classification carries legal and financial weight | Suggestions only, always reviewed by a licensed person; show reasoning and alternatives |
| Free-tier rate limits or model changes | Provider abstraction, a fallback model, response caching, small smoke subsets in CI |
| Overfitting prompts to your own eval set | Held-out test split that you only run for reporting |
| Prompt injection inside documents | Treat document text as data, strict schemas and enums, no tool access during extraction |
| Leaking client data | Real docs gitignored and redacted; no raw content in logs |
| Scope creep (WhatsApp bot, quotes, more doc types) | Finish invoices end to end with evals first; everything else waits for the "Later" row |
| Tariff PDF is hard to parse | Spend time on a robust parser; spot-check 50 rows by hand; version the parsed table |
