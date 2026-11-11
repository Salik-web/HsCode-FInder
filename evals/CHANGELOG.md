# Eval changelog

One entry per eval run: date, prompt version, model, key metrics, what changed and why. Numbers are seen-layout dev unless marked otherwise; each carries its document count.

## 2026-11-11: baseline v1, partial (quota)

- Model `gemini-3.5-flash` (Google AI Studio free tier), prompt `v1`, schema `1.0`.
- Reports: `reports/20261111T082937_synthetic_clean_dev_gemini-3.5-flash_v1.md`, `reports/20261111T083025_synthetic_messy_dev_gemini-3.5-flash_v1.md`.
- **Incomplete:** clean dev 10/53 scored, messy dev 2/16. The free tier allows 20 requests per model per day (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`), and the quota ran out. Rerunning the same commands scores the rest; the scored documents come from cache.
- On what was scored: critical-field accuracy 100% (clean n=10, messy n=2), every field 100%, line F1 100%, no retries or `needs_review`. Reference buyers ("Same as consignee"): 4/4 right. Latency p50 15.1 s, p95 25.7 s (clean). Cost $0 (free tier).
- Too few documents to read anything into yet; the full 53 + 16 decide the baseline.
- What changed: first run. The dataset now prints "Same as consignee" (or a synonym) in the buyer block for about 30% of buyers who are the consignee, and reports score that as its own slice.
- Model choice: `gemini-3.7-flash` and `gemini-3.8-flash` hung until timeout even on a two-word prompt, and `gemini-3.6-flash` returned 503, so `gemini-3.5-flash` is the primary.
