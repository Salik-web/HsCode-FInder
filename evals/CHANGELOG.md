# Eval changelog

One entry per eval run: date, prompt version, model, key metrics, what changed and why. Numbers are seen-layout dev unless marked otherwise; each carries its document count.

## 2026-11-12: v3 on the v2 baseline's 18 documents

- Model `qwen/qwen3.8-27b` on Groq, thinking off, JSON object mode, prompt `v3` (exact key list in the system prompt and the retry), key repair on, schema `1.0`, git `61b0030`.
- Reports: `reports/20261112T121955_synthetic_clean_dev_qwen_qwen3.8-27b_reasoning-none_v3.md`, `reports/20261112T122001_synthetic_messy_dev_qwen_qwen3.8-27b_reasoning-none_v3.md`. Same documents as the v2 baseline: the first 16 clean dev and the first 2 messy dev by id, all seen layouts.
- Clean (n=16), v2 -> v3: first-try valid JSON 8/16 -> 16/16, retries 8 -> 0, `needs_review` 2 -> 0, critical-field accuracy 85.1% -> 100%, all critical right 14/16 -> 16/16, all fields 96.1% -> 99.5%, tokens per document 5,686 -> 3,859 (-32%), latency p50 2.2 s -> 1.8 s.
- Messy (n=2), v2 -> v3: first-try valid 2/2 -> 1/2 (inv_00003_messy wrote amounts that weren't plain decimals; the retry fixed it), all critical right 1/2 both (incoterm CIP read as CIF in both versions: a reading error on a blurred photo).
- Keys repaired 0 and ignored 0 in v3: the key list in the prompt alone fixed key names. The repair code is a safety net; on the cached v2 answers it raises first-try validity from 10/18 to 14/18.
- New errors in v3 (both clean, both non-critical): inv_00002 (a "Same as consignee" buyer) has the buyer name "As consignee" with no address or country (v2 was right; reference buyers 6/6 -> 5/6); inv_00025 units "MET" for MT and "SE" for SET, the same garbling seen in v2 key names but in values, which key repair can't catch.
- Tokens per call: first-attempt input 3,211 -> 3,375 and output 529 -> 484 (clean). JSON object mode did not cut input by ~1,000 as expected; the saving per document comes from no retries and shorter output.
- Small sample (18 documents, one model, seen layouts only); one document moves a clean rate by 6 points.

## 2026-11-11: v2 baseline on Groq (partial by decision)

- Model `qwen/qwen3.8-27b` on Groq (free tier), thinking off (`reasoning_effort=none`), provider strict mode off, prompt `v2` (compact line items), schema `1.0`, git `ff12136`.
- Reports: `reports/20261111T094649_synthetic_clean_dev_qwen_qwen3.8-27b_reasoning-none_v2.md`, `reports/20261111T100956_synthetic_messy_dev_qwen_qwen3.8-27b_reasoning-none_v2.md`.
- **This is the v2 baseline, on 18 documents:** 16 clean dev (seen layouts) and 2 messy dev (seen). No unseen documents. Groq's 200,000 tokens a day stopped the run, and Salik chose not to spend about 2.5 days finishing it, because the failure mode was already clear.
- Clean dev, seen (n=16): critical-field accuracy 85.1%, all critical right 87.5% (14/16), line precision 100%, recall 84.0%, F1 91.3%, first-try valid JSON 50.0% (8/16), retry rate 50.0% (8/16), `needs_review` 12.5% (2/16). Latency p50 2.2 s, p95 5.1 s. Cost $0.
- Messy dev, seen (n=2): critical-field accuracy 97.1%, all critical right 50.0% (1/2), first-try valid JSON 100% (2/2), retry rate 0%.
- Every document with valid output was fully correct. Both clean failures are schema failures: invented keys survived the retry (`document_type` on inv_00025, where the retry repeated the first answer exactly; `country_of_origin` on inv_00003). First-try errors were misspelt keys (`shiftper`, `shift`, `" shipper"`, `buyer.	assertEquals`) and invented keys (`document_type`, `notes`, `company_name`, `country_of_origin`).
- What changed and why: model switched from Gemini (20 requests a day) to Groq to stay on the free tier; prompt v2 writes line items as arrays to fit Groq's 1,000 output tokens a minute.
- Next: Phase 3 starts early with schema reliability (prompt v3 plus key repair in code).

## 2026-11-11: baseline v1, partial (quota)

- Model `gemini-3.5-flash` (Google AI Studio free tier), prompt `v1`, schema `1.0`.
- Reports: `reports/20261111T082937_synthetic_clean_dev_gemini-3.5-flash_v1.md`, `reports/20261111T083025_synthetic_messy_dev_gemini-3.5-flash_v1.md`.
- **Incomplete:** clean dev 10/53 scored, messy dev 2/16. The free tier allows 20 requests per model per day (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`), and the quota ran out. Rerunning the same commands scores the rest; the scored documents come from cache.
- On what was scored: critical-field accuracy 100% (clean n=10, messy n=2), every field 100%, line F1 100%, no retries or `needs_review`. Reference buyers ("Same as consignee"): 4/4 right. Latency p50 15.1 s, p95 25.7 s (clean). Cost $0 (free tier).
- Too few documents to read anything into yet; the full 53 + 16 decide the baseline.
- What changed: first run. The dataset now prints "Same as consignee" (or a synonym) in the buyer block for about 30% of buyers who are the consignee, and reports score that as its own slice.
- Model choice: `gemini-3.7-flash` and `gemini-3.8-flash` hung until timeout even on a two-word prompt, and `gemini-3.6-flash` returned 503, so `gemini-3.5-flash` is the primary.
