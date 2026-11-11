# Eval run 20261111T082937_synthetic_clean_dev_gemini-3.5-flash_v1

- Dataset: `synthetic_clean`, split `dev`
- Model: `gemini-3.5-flash`, prompt `v1`, schema `1.0`
- Git: `de94f82ea3 (uncommitted changes)`, run at 2026-11-11 08:29 UTC
- Documents scored: 10 of 53; LLM calls made: 5, from cache: 5

> **INCOMPLETE:** 43 documents not scored (rate-limited or unreachable). Rerun the same command later; scored documents come from cache.

## Headline (seen and unseen layouts are never blended)

| Metric | Seen | Unseen |
| --- | --- | --- |
| Documents scored | 10 | 0 |
| Critical-field accuracy | 100.0% | — |
| All critical fields right | 100.0% | — |
| Line precision | 100.0% | — |
| Line recall | 100.0% | — |
| Line F1 | 100.0% | — |
| needs_review rate | 0.0% | — |
| Failed rate | 0.0% | — |
| Retry rate | 0.0% | — |
| Cost per document | $0.00000 | unknown |
| Latency p50 | 15.1 s | — |
| Latency p95 | 25.7 s | — |

## Per-field accuracy (correct/total; line fields on matched lines only)

| Field | Seen | Unseen | Seen missed / hallucinated |
| --- | --- | --- | --- |
| `invoice_number` | 100.0% (10/10) | — (0) | 0 / 0 |
| `invoice_date` | 100.0% (10/10) | — (0) | 0 / 0 |
| `incoterm` | 100.0% (10/10) | — (0) | 0 / 0 |
| `currency` | 100.0% (10/10) | — (0) | 0 / 0 |
| `total_amount` | 100.0% (10/10) | — (0) | 0 / 0 |
| `gross_weight_kg` | 100.0% (10/10) | — (0) | 0 / 0 |
| `net_weight_kg` | 100.0% (10/10) | — (0) | 0 / 0 |
| `shipper.name` | 100.0% (10/10) | — (0) | 0 / 0 |
| `shipper.address` | 100.0% (10/10) | — (0) | 0 / 0 |
| `shipper.country` | 100.0% (10/10) | — (0) | 0 / 0 |
| `buyer.name` | 100.0% (10/10) | — (0) | 0 / 0 |
| `buyer.address` | 100.0% (10/10) | — (0) | 0 / 0 |
| `buyer.country` | 100.0% (10/10) | — (0) | 0 / 0 |
| `consignee.name` | 100.0% (10/10) | — (0) | 0 / 0 |
| `consignee.address` | 100.0% (10/10) | — (0) | 0 / 0 |
| `consignee.country` | 100.0% (10/10) | — (0) | 0 / 0 |
| `notify_party.name` | 100.0% (10/10) | — (0) | 0 / 0 |
| `notify_party.address` | 100.0% (10/10) | — (0) | 0 / 0 |
| `notify_party.country` | 100.0% (10/10) | — (0) | 0 / 0 |
| `line.description` | 100.0% (85/85) | — (0) | 0 / 0 |
| `line.quantity` | 100.0% (85/85) | — (0) | 0 / 0 |
| `line.unit` | 100.0% (85/85) | — (0) | 0 / 0 |
| `line.unit_price` | 100.0% (85/85) | — (0) | 0 / 0 |
| `line.amount` | 100.0% (85/85) | — (0) | 0 / 0 |
| `line.hs_code_on_doc` | 100.0% (85/85) | — (0) | 0 / 0 |

## By template

| Template | Documents | Critical-field accuracy | All critical right | Line F1 |
| --- | --- | --- | --- | --- |
| airy | 2 | 100.0% | 100.0% | 100.0% |
| boxed | 1 | 100.0% | 100.0% | 100.0% |
| classic | 1 | 100.0% | 100.0% | 100.0% |
| compact | 2 | 100.0% | 100.0% | 100.0% |
| modern | 2 | 100.0% | 100.0% | 100.0% |
| summary | 2 | 100.0% | 100.0% | 100.0% |

## Buyer by how it is printed

`reference`: the buyer block says e.g. "Same as consignee" and the truth is the consignee's details. `repeated`: the same company printed in full. `distinct`: a different company.

| Buyer form | Field | Seen | Unseen |
| --- | --- | --- | --- |
| reference | `buyer.name` | 100.0% (4/4) | — (0) |
| reference | `buyer.address` | 100.0% (4/4) | — (0) |
| reference | `buyer.country` | 100.0% (4/4) | — (0) |
| repeated | `buyer.name` | 100.0% (4/4) | — (0) |
| repeated | `buyer.address` | 100.0% (4/4) | — (0) |
| repeated | `buyer.country` | 100.0% (4/4) | — (0) |
| distinct | `buyer.name` | 100.0% (2/2) | — (0) |
| distinct | `buyer.address` | 100.0% (2/2) | — (0) |
| distinct | `buyer.country` | 100.0% (2/2) | — (0) |

## Change since the previous run

No earlier run on this dataset and split.

## 0 worst failures (of 0 documents with any error)

### Not scored

- inv_00016: RateLimitedError: gemini rate limit: Error code: 429
- inv_00017: RateLimitedError: gemini rate limit: Error code: 429
- inv_00018: skipped after the LLM became unavailable
- inv_00023: skipped after the LLM became unavailable
- inv_00024: skipped after the LLM became unavailable
- inv_00025: skipped after the LLM became unavailable
- inv_00026: skipped after the LLM became unavailable
- inv_00027: skipped after the LLM became unavailable
- inv_00029: skipped after the LLM became unavailable
- inv_00032: skipped after the LLM became unavailable
- inv_00034: skipped after the LLM became unavailable
- inv_00035: skipped after the LLM became unavailable
- inv_00039: skipped after the LLM became unavailable
- inv_00041: skipped after the LLM became unavailable
- inv_00042: skipped after the LLM became unavailable
- inv_00043: skipped after the LLM became unavailable
- inv_00045: skipped after the LLM became unavailable
- inv_00047: skipped after the LLM became unavailable
- inv_00049: skipped after the LLM became unavailable
- inv_00050: skipped after the LLM became unavailable
- inv_00051: skipped after the LLM became unavailable
- inv_00053: skipped after the LLM became unavailable
- inv_00056: skipped after the LLM became unavailable
- inv_00057: skipped after the LLM became unavailable
- inv_00058: skipped after the LLM became unavailable
- inv_00061: skipped after the LLM became unavailable
- inv_00064: skipped after the LLM became unavailable
- inv_00065: skipped after the LLM became unavailable
- inv_00066: skipped after the LLM became unavailable
- inv_00067: skipped after the LLM became unavailable
- inv_00074: skipped after the LLM became unavailable
- inv_00077: skipped after the LLM became unavailable
- inv_00079: skipped after the LLM became unavailable
- inv_00080: skipped after the LLM became unavailable
- inv_00081: skipped after the LLM became unavailable
- inv_00085: skipped after the LLM became unavailable
- inv_00087: skipped after the LLM became unavailable
- inv_00088: skipped after the LLM became unavailable
- inv_00095: skipped after the LLM became unavailable
- inv_00096: skipped after the LLM became unavailable
- inv_00097: skipped after the LLM became unavailable
- inv_00098: skipped after the LLM became unavailable
- inv_00099: skipped after the LLM became unavailable
