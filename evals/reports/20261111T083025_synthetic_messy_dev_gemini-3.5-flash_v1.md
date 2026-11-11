# Eval run 20261111T083025_synthetic_messy_dev_gemini-3.5-flash_v1

- Dataset: `synthetic_messy`, split `dev`
- Model: `gemini-3.5-flash`, prompt `v1`, schema `1.0`
- Git: `de94f82ea3 (uncommitted changes)`, run at 2026-11-11 08:30 UTC
- Documents scored: 2 of 16; LLM calls made: 0, from cache: 2

> **INCOMPLETE:** 14 documents not scored (rate-limited or unreachable). Rerun the same command later; scored documents come from cache.

## Headline (seen and unseen layouts are never blended)

| Metric | Seen | Unseen |
| --- | --- | --- |
| Documents scored | 2 | 0 |
| Critical-field accuracy | 100.0% | — |
| All critical fields right | 100.0% | — |
| Line precision | 100.0% | — |
| Line recall | 100.0% | — |
| Line F1 | 100.0% | — |
| needs_review rate | 0.0% | — |
| Failed rate | 0.0% | — |
| Retry rate | 0.0% | — |
| Cost per document | $0.00000 | unknown |
| Latency p50 | 24.6 s | — |
| Latency p95 | 27.5 s | — |

## Per-field accuracy (correct/total; line fields on matched lines only)

| Field | Seen | Unseen | Seen missed / hallucinated |
| --- | --- | --- | --- |
| `invoice_number` | 100.0% (2/2) | — (0) | 0 / 0 |
| `invoice_date` | 100.0% (2/2) | — (0) | 0 / 0 |
| `incoterm` | 100.0% (2/2) | — (0) | 0 / 0 |
| `currency` | 100.0% (2/2) | — (0) | 0 / 0 |
| `total_amount` | 100.0% (2/2) | — (0) | 0 / 0 |
| `gross_weight_kg` | 100.0% (2/2) | — (0) | 0 / 0 |
| `net_weight_kg` | 100.0% (2/2) | — (0) | 0 / 0 |
| `shipper.name` | 100.0% (2/2) | — (0) | 0 / 0 |
| `shipper.address` | 100.0% (2/2) | — (0) | 0 / 0 |
| `shipper.country` | 100.0% (2/2) | — (0) | 0 / 0 |
| `buyer.name` | 100.0% (2/2) | — (0) | 0 / 0 |
| `buyer.address` | 100.0% (2/2) | — (0) | 0 / 0 |
| `buyer.country` | 100.0% (2/2) | — (0) | 0 / 0 |
| `consignee.name` | 100.0% (2/2) | — (0) | 0 / 0 |
| `consignee.address` | 100.0% (2/2) | — (0) | 0 / 0 |
| `consignee.country` | 100.0% (2/2) | — (0) | 0 / 0 |
| `notify_party.name` | 100.0% (2/2) | — (0) | 0 / 0 |
| `notify_party.address` | 100.0% (2/2) | — (0) | 0 / 0 |
| `notify_party.country` | 100.0% (2/2) | — (0) | 0 / 0 |
| `line.description` | 100.0% (27/27) | — (0) | 0 / 0 |
| `line.quantity` | 100.0% (27/27) | — (0) | 0 / 0 |
| `line.unit` | 100.0% (27/27) | — (0) | 0 / 0 |
| `line.unit_price` | 100.0% (27/27) | — (0) | 0 / 0 |
| `line.amount` | 100.0% (27/27) | — (0) | 0 / 0 |
| `line.hs_code_on_doc` | 100.0% (27/27) | — (0) | 0 / 0 |

## By template

| Template | Documents | Critical-field accuracy | All critical right | Line F1 |
| --- | --- | --- | --- | --- |
| airy | 1 | 100.0% | 100.0% | 100.0% |
| boxed | 0 | — | — | — |
| classic | 1 | 100.0% | 100.0% | 100.0% |
| compact | 0 | — | — | — |
| modern | 0 | — | — | — |
| summary | 0 | — | — | — |

## Buyer by how it is printed

`reference`: the buyer block says e.g. "Same as consignee" and the truth is the consignee's details. `repeated`: the same company printed in full. `distinct`: a different company.

| Buyer form | Field | Seen | Unseen |
| --- | --- | --- | --- |
| repeated | `buyer.name` | 100.0% (2/2) | — (0) |
| repeated | `buyer.address` | 100.0% (2/2) | — (0) |
| repeated | `buyer.country` | 100.0% (2/2) | — (0) |

## Change since the previous run

No earlier run on this dataset and split.

## 0 worst failures (of 0 documents with any error)

### Not scored

- inv_00011_messy: RateLimitedError: gemini rate limit: Error code: 429
- inv_00026_messy: RateLimitedError: gemini rate limit: Error code: 429
- inv_00034_messy: skipped after the LLM became unavailable
- inv_00043_messy: skipped after the LLM became unavailable
- inv_00045_messy: skipped after the LLM became unavailable
- inv_00049_messy: skipped after the LLM became unavailable
- inv_00056_messy: skipped after the LLM became unavailable
- inv_00058_messy: skipped after the LLM became unavailable
- inv_00061_messy: skipped after the LLM became unavailable
- inv_00077_messy: skipped after the LLM became unavailable
- inv_00080_messy: skipped after the LLM became unavailable
- inv_00081_messy: skipped after the LLM became unavailable
- inv_00095_messy: skipped after the LLM became unavailable
- inv_00096_messy: skipped after the LLM became unavailable
