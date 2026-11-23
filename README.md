# TradeDoc AI

TradeDoc AI reads commercial invoices (PDFs or phone photos) and turns them into validated, structured data. A vision LLM extracts the fields. Deterministic code then checks them, and a reviewer approves the result before export. The system also suggests Pakistan Customs Tariff (PCT) codes for each line item, using retrieval over the official tariff plus an LLM reranker.

**The point of the project is evals and guardrails, not the UI.** Every change is measured on labelled data, and the numbers below come from versioned reports in [`evals/reports`](evals/reports). What changed between runs, and why, is in [`evals/CHANGELOG.md`](evals/CHANGELOG.md).

It is built for a freight-forwarding business (H & J Corporation), where clearing agents re-type invoice data into customs filings and quotes.

## How it works

```
upload ─► API stores the file (sha256) ─► worker:
            1. preprocess   PDF → page images + text layer (pypdfium2)
            2. extract      vision LLM → JSON → Pydantic; one retry with the errors, else needs_review
            3. rules        exact code synonyms, number formats
            4. guardrails   line maths, totals, net ≤ gross, ISO codes, dates, HS code exists,
                            name/address boundary against the text layer
            5. confidence   from deterministic signals only (never the model's own score)
            6. HS codes     query rewrite → vector + full-text retrieval over the PCT → rerank
          ─► review UI: fields next to the page, corrections logged, approve, export JSON/CSV
```

- **The LLM proposes; code verifies.** Arithmetic, formats and cross-field checks are plain Python. A suggested HS code that isn't among the retrieved tariff rows is rejected.
- **Explicit outcomes.** Every field is `ok`, `needs_review` or `failed_check`. A document without a text layer (a photo) sends its critical fields to a person, because nothing can verify them.
- **Reproducible.** Documents are addressed by sha256. LLM answers are cached by file, model, prompt version and the full request. Every result records model, prompt and schema versions.
- **Free tiers only.** Groq (`qwen/qwen3.8-27b` for extraction, `openai/gpt-oss-120b` for HS), local `bge-small` embeddings, Postgres + pgvector.

## Results

All results are on **synthetic invoices**: 9 layouts, generated with ground truth. They measure layout generalisation, not real-world accuracy. A set of redacted real documents is planned.

**Extraction** (`qwen/qwen3.8-27b`, prompt v3). Critical fields are the invoice number, total, currency, Incoterm, buyer and consignee names, and every line amount.

| Set | Documents | Critical-field accuracy | All critical fields right |
| --- | --- | --- | --- |
| Seen layouts, clean, dev: **a 27-document sample of the 53** | 27 | 100% | 27/27 |
| Seen layouts, photos (blurred, rotated), dev | 16 | 94.5% | 10/16 |
| Unseen layouts, clean (frozen before inspection) | 24 | 99.1% | 22/24 |
| Unseen layouts, photos (frozen before inspection) | 7 | 98.8% | 6/7 |
| Held-out layout never used for tuning, clean | 12 | 100% | 12/12 |
| Held-out layout never used for tuning, photos | 4 | 98.4% | 3/4 |

**Guardrails.** 60 invoices with one planted error each (wrong totals, net over gross, outdated Incoterms, future dates, invalid currencies): **60/60 caught**, and 0 of 53 correct invoices flagged.

**HS code suggestion** (`openai/gpt-oss-120b`, 86 invoice-style lines with gold PCT codes; the gold codes are not yet verified by a licensed customs agent):

| Split | Items | Top-3, 6-digit HS | Top-3, 4-digit heading | Top-3, 8-digit PCT |
| --- | --- | --- | --- | --- |
| Dev (tuned on) | 63 | 81.0% | 90.5% | 77.8% |
| Test (run once, frozen) | 23 | 78.3% | 82.6% | 73.9% |

Retrieval is the bottleneck. When the right code is among the 10 candidates, the reranker almost always puts it in its top 3. The tuning steps and their measured effect are in the changelog.

Suggestions are only suggestions: a licensed person makes the final classification.

## Run it locally

Needs Python 3.12, [uv](https://docs.astral.sh/uv/) and Docker. Put API keys in `.env`; see `.env.example`.

```bash
uv sync
docker compose up -d                       # Postgres + pgvector
uv run alembic upgrade head
uv run python -m tariff.ingest --csv tariff/pct_2026-27.csv --version 2026-27   # embed the tariff (~2 min)

uv run uvicorn app.api.main:create_app --factory   # API on :8000 (docs at /docs)
uv run python -m app.worker                         # processes uploads
uv run streamlit run ui/app.py                      # review UI on :8501

uv run pytest                                       # tests (LLM always mocked)
```

To reproduce the evals, generate the synthetic data with `uv run python -m synth.generate --n 100 --messy 30 --seed 42`, then use the commands in `CLAUDE.md`.

## Repository

| Path | What it holds |
| --- | --- |
| `app/pipeline/` | Pure pipeline functions: preprocess, extract, rules, validate, confidence, HS suggestion, review |
| `app/api/`, `app/worker.py`, `ui/` | FastAPI, the worker, the Streamlit review UI |
| `app/db/` | SQLAlchemy models, Alembic migrations, pgvector index |
| `evals/` | Metrics, eval CLIs, reports, changelog, `hs_gold` |
| `synth/` | Synthetic invoice generator (layouts, rendering, photo noise, planted errors) |
| `tariff/` | PCT 2026-27 parser and the parsed table |
| `prompts/` | Versioned prompts; an old version is never edited |
