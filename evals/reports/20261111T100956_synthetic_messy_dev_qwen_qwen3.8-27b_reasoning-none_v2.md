# Eval run 20261111T100956_synthetic_messy_dev_qwen_qwen3.8-27b_reasoning-none_v2

- Dataset: `synthetic_messy`, split `dev`
- Model: `qwen/qwen3.8-27b`, reasoning `none`, prompt `v2`, schema `1.0`
- Git: `ff12136f86 (uncommitted changes)`, run at 2026-11-11 10:09 UTC
- Documents scored: 2 of 16; LLM calls made: 1, from cache: 1

> **INCOMPLETE:** 14 documents not scored (rate-limited or unreachable). Rerun the same command later; scored documents come from cache.

## Headline (seen and unseen layouts are never blended)

| Metric | Seen | Unseen |
| --- | --- | --- |
| Documents scored | 2 | 0 |
| Critical-field accuracy | 97.1% | — |
| All critical fields right | 50.0% | — |
| Line precision | 100.0% | — |
| Line recall | 100.0% | — |
| Line F1 | 100.0% | — |
| needs_review rate | 0.0% | — |
| Failed rate | 0.0% | — |
| First-try valid JSON | 100.0% (2/2) | — (0/0) |
| Retry rate | 0.0% (0/2) | — (0/0) |
| Cost per document | $0.00000 | — |
| Latency p50 | 2.0 s | — |
| Latency p95 | 8.1 s | — |

## Per-field accuracy (correct/total; line fields on matched lines only)

| Field | Seen | Unseen | Seen missed / hallucinated |
| --- | --- | --- | --- |
| `invoice_number` | 100.0% (2/2) | — (0) | 0 / 0 |
| `invoice_date` | 100.0% (2/2) | — (0) | 0 / 0 |
| `incoterm` | 50.0% (1/2) | — (0) | 0 / 0 |
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
| airy | 1 | 93.8% | 0.0% | 100.0% |
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

Previous: `20261111T083025_synthetic_messy_dev_gemini-3.5-flash_v1` (model `gemini-3.5-flash`, prompt `v1`, 2/16 scored). Points of change:

| Metric | Seen | Unseen |
| --- | --- | --- |
| Critical-field accuracy | -2.9 | — |
| All critical right | -50.0 | — |
| Line F1 | +0.0 | — |

| Group | Field | Points |
| --- | --- | --- |
| seen | `incoterm` | -50.0 |

## 1 worst failures (of 1 documents with any error)

### inv_00003_messy (airy, buyer repeated, extract ok)

1 critical and 1 other fields not correct. Lines: 12 true, 12 predicted, 12 matched.

| Field | Outcome | Expected | Predicted |
| --- | --- | --- | --- |
| `incoterm` | wrong | CIP | CIF |

### Not scored

- inv_00011_messy: RateLimitedError: groq rate limit: Error code: 429 - Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01kqptx4pzfhns3x8ezck1c0af` service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 199880, Requested 3637. Please try again in 25m19.344s. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing
- inv_00026_messy: RateLimitedError: groq rate limit: Error code: 429 - Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01kqptx4pzfhns3x8ezck1c0af` service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 199741, Requested 3770. Please try again in 25m16.752s. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing
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
