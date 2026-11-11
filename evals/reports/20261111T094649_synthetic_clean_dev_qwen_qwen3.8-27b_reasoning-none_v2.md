# Eval run 20261111T094649_synthetic_clean_dev_qwen_qwen3.8-27b_reasoning-none_v2

- Dataset: `synthetic_clean`, split `dev`
- Model: `qwen/qwen3.8-27b`, reasoning `none`, prompt `v2`, schema `1.0`
- Git: `ff12136f86`, run at 2026-11-11 09:46 UTC
- Documents scored: 16 of 53; LLM calls made: 22, from cache: 3

> **INCOMPLETE:** 37 documents not scored (rate-limited or unreachable). Rerun the same command later; scored documents come from cache.

## Headline (seen and unseen layouts are never blended)

| Metric | Seen | Unseen |
| --- | --- | --- |
| Documents scored | 16 | 0 |
| Critical-field accuracy | 85.1% | — |
| All critical fields right | 87.5% | — |
| Line precision | 100.0% | — |
| Line recall | 84.0% | — |
| Line F1 | 91.3% | — |
| needs_review rate | 12.5% | — |
| Failed rate | 0.0% | — |
| First-try valid JSON | 50.0% (8/16) | — (0/0) |
| Retry rate | 50.0% (8/16) | — (0/0) |
| Cost per document | $0.00000 | — |
| Latency p50 | 2.2 s | — |
| Latency p95 | 5.1 s | — |

## Per-field accuracy (correct/total; line fields on matched lines only)

| Field | Seen | Unseen | Seen missed / hallucinated |
| --- | --- | --- | --- |
| `invoice_number` | 87.5% (14/16) | — (0) | 0 / 0 |
| `invoice_date` | 87.5% (14/16) | — (0) | 0 / 0 |
| `incoterm` | 87.5% (14/16) | — (0) | 0 / 0 |
| `currency` | 87.5% (14/16) | — (0) | 0 / 0 |
| `total_amount` | 87.5% (14/16) | — (0) | 0 / 0 |
| `gross_weight_kg` | 87.5% (14/16) | — (0) | 0 / 0 |
| `net_weight_kg` | 87.5% (14/16) | — (0) | 0 / 0 |
| `shipper.name` | 87.5% (14/16) | — (0) | 0 / 0 |
| `shipper.address` | 87.5% (14/16) | — (0) | 0 / 0 |
| `shipper.country` | 87.5% (14/16) | — (0) | 0 / 0 |
| `buyer.name` | 87.5% (14/16) | — (0) | 0 / 0 |
| `buyer.address` | 87.5% (14/16) | — (0) | 0 / 0 |
| `buyer.country` | 87.5% (14/16) | — (0) | 0 / 0 |
| `consignee.name` | 87.5% (14/16) | — (0) | 0 / 0 |
| `consignee.address` | 87.5% (14/16) | — (0) | 0 / 0 |
| `consignee.country` | 87.5% (14/16) | — (0) | 0 / 0 |
| `notify_party.name` | 87.5% (14/16) | — (0) | 0 / 0 |
| `notify_party.address` | 87.5% (14/16) | — (0) | 0 / 0 |
| `notify_party.country` | 87.5% (14/16) | — (0) | 0 / 0 |
| `line.description` | 100.0% (110/110) | — (0) | 0 / 0 |
| `line.quantity` | 100.0% (110/110) | — (0) | 0 / 0 |
| `line.unit` | 100.0% (110/110) | — (0) | 0 / 0 |
| `line.unit_price` | 100.0% (110/110) | — (0) | 0 / 0 |
| `line.amount` | 100.0% (110/110) | — (0) | 0 / 0 |
| `line.hs_code_on_doc` | 100.0% (110/110) | — (0) | 0 / 0 |

## By template

| Template | Documents | Critical-field accuracy | All critical right | Line F1 |
| --- | --- | --- | --- | --- |
| airy | 2 | 38.5% | 50.0% | 50.0% |
| boxed | 1 | 100.0% | 100.0% | 100.0% |
| classic | 3 | 100.0% | 100.0% | 100.0% |
| compact | 3 | 100.0% | 100.0% | 100.0% |
| modern | 4 | 73.5% | 75.0% | 84.2% |
| summary | 3 | 100.0% | 100.0% | 100.0% |

## Buyer by how it is printed

`reference`: the buyer block says e.g. "Same as consignee" and the truth is the consignee's details. `repeated`: the same company printed in full. `distinct`: a different company.

| Buyer form | Field | Seen | Unseen |
| --- | --- | --- | --- |
| reference | `buyer.name` | 100.0% (6/6) | — (0) |
| reference | `buyer.address` | 100.0% (6/6) | — (0) |
| reference | `buyer.country` | 100.0% (6/6) | — (0) |
| repeated | `buyer.name` | 66.7% (4/6) | — (0) |
| repeated | `buyer.address` | 66.7% (4/6) | — (0) |
| repeated | `buyer.country` | 66.7% (4/6) | — (0) |
| distinct | `buyer.name` | 100.0% (4/4) | — (0) |
| distinct | `buyer.address` | 100.0% (4/4) | — (0) |
| distinct | `buyer.country` | 100.0% (4/4) | — (0) |

## Change since the previous run

Previous: `20261111T082937_synthetic_clean_dev_gemini-3.5-flash_v1` (model `gemini-3.5-flash`, prompt `v1`, 10/53 scored). Points of change:

| Metric | Seen | Unseen |
| --- | --- | --- |
| Critical-field accuracy | -14.9 | — |
| All critical right | -12.5 | — |
| Line F1 | -8.7 | — |

| Group | Field | Points |
| --- | --- | --- |
| seen | `invoice_number` | -12.5 |
| seen | `invoice_date` | -12.5 |
| seen | `incoterm` | -12.5 |
| seen | `currency` | -12.5 |
| seen | `total_amount` | -12.5 |
| seen | `gross_weight_kg` | -12.5 |
| seen | `net_weight_kg` | -12.5 |
| seen | `shipper.name` | -12.5 |
| seen | `shipper.address` | -12.5 |
| seen | `shipper.country` | -12.5 |
| seen | `buyer.name` | -12.5 |
| seen | `buyer.address` | -12.5 |
| seen | `buyer.country` | -12.5 |
| seen | `consignee.name` | -12.5 |
| seen | `consignee.address` | -12.5 |
| seen | `consignee.country` | -12.5 |
| seen | `notify_party.name` | -12.5 |
| seen | `notify_party.address` | -12.5 |
| seen | `notify_party.country` | -12.5 |

## 2 worst failures (of 2 documents with any error)

### inv_00003 (airy, buyer repeated, extract needs_review)

16 critical and 19 other fields not correct. Lines: 12 true, 0 predicted, 0 matched.

Error: output failed validation 2 times

| Field | Outcome | Expected | Predicted |
| --- | --- | --- | --- |
| `invoice_number` | no_output | INV-2025-57843 | null |
| `invoice_date` | no_output | 2025-12-11 | null |
| `incoterm` | no_output | CIP | null |
| `currency` | no_output | SGD | null |
| `total_amount` | no_output | 870631.28 | null |
| `gross_weight_kg` | no_output | 12488.93 | null |
| `net_weight_kg` | no_output | 11457.73 | null |
| `shipper.name` | no_output | Wensen & van Klaarwater | null |
| `shipper.address` | no_output | Mayapad 56 8193PS De Glind | null |
| `shipper.country` | no_output | NL | null |
| `buyer.name` | no_output | Ibrahim, Rabb and Gulshan | null |
| `buyer.address` | no_output | Plot 102, Block E, Sialkot 65310 | null |
| `buyer.country` | no_output | PK | null |
| `consignee.name` | no_output | Ibrahim, Rabb and Gulshan | null |
| `consignee.address` | no_output | Plot 102, Block E, Sialkot 65310 | null |
| `consignee.country` | no_output | PK | null |
| `notify_party.name` | no_output | null | null |
| `notify_party.address` | no_output | null | null |
| `notify_party.country` | no_output | null | null |
| `line.amount` | no_output | 207732.88 | null |
| `line.amount` | no_output | 39863.22 | null |
| `line.amount` | no_output | 160323.69 | null |
| `line.amount` | no_output | 16332.76 | null |
| `line.amount` | no_output | 24888.51 | null |
| `line.amount` | no_output | 63196.80 | null |
| `line.amount` | no_output | 159342.82 | null |
| `line.amount` | no_output | 106904.18 | null |
| `line.amount` | no_output | 40134.36 | null |
| `line.amount` | no_output | 23278.05 | null |
| `line.amount` | no_output | 22909.77 | null |
| `line.amount` | no_output | 5724.24 | null |

### inv_00025 (modern, buyer repeated, extract needs_review)

13 critical and 19 other fields not correct. Lines: 9 true, 0 predicted, 0 matched.

Error: output failed validation 2 times

| Field | Outcome | Expected | Predicted |
| --- | --- | --- | --- |
| `invoice_number` | no_output | 972-97956 | null |
| `invoice_date` | no_output | 2025-10-24 | null |
| `incoterm` | no_output | CPT | null |
| `currency` | no_output | AED | null |
| `total_amount` | no_output | 292149.40 | null |
| `gross_weight_kg` | no_output | 6574.10 | null |
| `net_weight_kg` | no_output | 6087.13 | null |
| `shipper.name` | no_output | Lahiah, Quadir and Awmar | null |
| `shipper.address` | no_output | Plot 68, Block D, Peshawar 23889 | null |
| `shipper.country` | no_output | PK | null |
| `buyer.name` | no_output | Zanichelli e figli | null |
| `buyer.address` | no_output | Via Campano, 235 41048, Saltino (MO) | null |
| `buyer.country` | no_output | IT | null |
| `consignee.name` | no_output | Zanichelli e figli | null |
| `consignee.address` | no_output | Via Campano, 235 41048, Saltino (MO) | null |
| `consignee.country` | no_output | IT | null |
| `notify_party.name` | no_output | Renzi e figli | null |
| `notify_party.address` | no_output | Viale Ermanno, 20 17022, Borgio Verezzi (SV) | null |
| `notify_party.country` | no_output | IT | null |
| `line.amount` | no_output | 1190.65 | null |
| `line.amount` | no_output | 8062.65 | null |
| `line.amount` | no_output | 9756.64 | null |
| `line.amount` | no_output | 14055.60 | null |
| `line.amount` | no_output | 60597.18 | null |
| `line.amount` | no_output | 158782.02 | null |
| `line.amount` | no_output | 357.44 | null |
| `line.amount` | no_output | 4042.62 | null |
| `line.amount` | no_output | 35304.60 | null |

### Not scored

- inv_00026: RateLimitedError: groq rate limit: Error code: 429 - Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01kqptx4pzfhns3x8ezck1c0af` service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 196388, Requested 4431. Please try again in 5m53.808s. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing
- inv_00027: RateLimitedError: groq rate limit: Error code: 429 - Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_01kqptx4pzfhns3x8ezck1c0af` service tier `on_demand` on tokens per day (TPD): Limit 200000, Used 196527, Requested 6395. Please try again in 21m2.304s. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing
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
