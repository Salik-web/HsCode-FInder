# Eval run 20261112T121955_synthetic_clean_dev_qwen_qwen3.8-27b_reasoning-none_v3

- Dataset: `synthetic_clean`, split `dev`
- Model: `qwen/qwen3.8-27b`, reasoning `none`, prompt `v3`, schema `1.0`
- Git: `61b00303c4`, run at 2026-11-12 12:19 UTC
- Documents scored: 16 of 16; LLM calls made: 0, from cache: 16

## Headline (seen and unseen layouts are never blended)

| Metric | Seen | Unseen |
| --- | --- | --- |
| Documents scored | 16 | 0 |
| Critical-field accuracy | 100.0% | — |
| All critical fields right | 100.0% | — |
| Line precision | 100.0% | — |
| Line recall | 100.0% | — |
| Line F1 | 100.0% | — |
| needs_review rate | 0.0% | — |
| Failed rate | 0.0% | — |
| First-try valid JSON | 100.0% (16/16) | — (0/0) |
| Retry rate | 0.0% (0/16) | — (0/0) |
| Keys repaired | 0 | 0 |
| Keys ignored | 0 | 0 |
| Cost per document | $0.00000 | — |
| Latency p50 | 1.8 s | — |
| Latency p95 | 42.9 s | — |

## Per-field accuracy (correct/total; line fields on matched lines only)

| Field | Seen | Unseen | Seen missed / hallucinated |
| --- | --- | --- | --- |
| `invoice_number` | 100.0% (16/16) | — (0) | 0 / 0 |
| `invoice_date` | 100.0% (16/16) | — (0) | 0 / 0 |
| `incoterm` | 100.0% (16/16) | — (0) | 0 / 0 |
| `currency` | 100.0% (16/16) | — (0) | 0 / 0 |
| `total_amount` | 100.0% (16/16) | — (0) | 0 / 0 |
| `gross_weight_kg` | 100.0% (16/16) | — (0) | 0 / 0 |
| `net_weight_kg` | 100.0% (16/16) | — (0) | 0 / 0 |
| `shipper.name` | 100.0% (16/16) | — (0) | 0 / 0 |
| `shipper.address` | 100.0% (16/16) | — (0) | 0 / 0 |
| `shipper.country` | 100.0% (16/16) | — (0) | 0 / 0 |
| `buyer.name` | 93.8% (15/16) | — (0) | 0 / 0 |
| `buyer.address` | 93.8% (15/16) | — (0) | 1 / 0 |
| `buyer.country` | 93.8% (15/16) | — (0) | 1 / 0 |
| `consignee.name` | 100.0% (16/16) | — (0) | 0 / 0 |
| `consignee.address` | 100.0% (16/16) | — (0) | 0 / 0 |
| `consignee.country` | 100.0% (16/16) | — (0) | 0 / 0 |
| `notify_party.name` | 100.0% (16/16) | — (0) | 0 / 0 |
| `notify_party.address` | 100.0% (16/16) | — (0) | 0 / 0 |
| `notify_party.country` | 100.0% (16/16) | — (0) | 0 / 0 |
| `line.description` | 100.0% (131/131) | — (0) | 0 / 0 |
| `line.quantity` | 100.0% (131/131) | — (0) | 0 / 0 |
| `line.unit` | 98.5% (129/131) | — (0) | 0 / 0 |
| `line.unit_price` | 100.0% (131/131) | — (0) | 0 / 0 |
| `line.amount` | 100.0% (131/131) | — (0) | 0 / 0 |
| `line.hs_code_on_doc` | 100.0% (131/131) | — (0) | 0 / 0 |

## By template

| Template | Documents | Critical-field accuracy | All critical right | Line F1 |
| --- | --- | --- | --- | --- |
| airy | 2 | 100.0% | 100.0% | 100.0% |
| boxed | 1 | 100.0% | 100.0% | 100.0% |
| classic | 3 | 100.0% | 100.0% | 100.0% |
| compact | 3 | 100.0% | 100.0% | 100.0% |
| modern | 4 | 100.0% | 100.0% | 100.0% |
| summary | 3 | 100.0% | 100.0% | 100.0% |

## Buyer by how it is printed

`reference`: the buyer block says e.g. "Same as consignee" and the truth is the consignee's details. `repeated`: the same company printed in full. `distinct`: a different company.

| Buyer form | Field | Seen | Unseen |
| --- | --- | --- | --- |
| reference | `buyer.name` | 83.3% (5/6) | — (0) |
| reference | `buyer.address` | 83.3% (5/6) | — (0) |
| reference | `buyer.country` | 83.3% (5/6) | — (0) |
| repeated | `buyer.name` | 100.0% (6/6) | — (0) |
| repeated | `buyer.address` | 100.0% (6/6) | — (0) |
| repeated | `buyer.country` | 100.0% (6/6) | — (0) |
| distinct | `buyer.name` | 100.0% (4/4) | — (0) |
| distinct | `buyer.address` | 100.0% (4/4) | — (0) |
| distinct | `buyer.country` | 100.0% (4/4) | — (0) |

## Change since the previous run

Previous: `20261111T094649_synthetic_clean_dev_qwen_qwen3.8-27b_reasoning-none_v2` (model `qwen/qwen3.8-27b`, prompt `v2`, 16/53 scored). Points of change:

| Metric | Seen | Unseen |
| --- | --- | --- |
| Critical-field accuracy | +14.9 | — |
| All critical right | +12.5 | — |
| Line F1 | +8.7 | — |

| Group | Field | Points |
| --- | --- | --- |
| seen | `invoice_number` | +12.5 |
| seen | `invoice_date` | +12.5 |
| seen | `incoterm` | +12.5 |
| seen | `currency` | +12.5 |
| seen | `total_amount` | +12.5 |
| seen | `gross_weight_kg` | +12.5 |
| seen | `net_weight_kg` | +12.5 |
| seen | `shipper.name` | +12.5 |
| seen | `shipper.address` | +12.5 |
| seen | `shipper.country` | +12.5 |
| seen | `buyer.name` | +6.2 |
| seen | `buyer.address` | +6.2 |
| seen | `buyer.country` | +6.2 |
| seen | `consignee.name` | +12.5 |
| seen | `consignee.address` | +12.5 |
| seen | `consignee.country` | +12.5 |
| seen | `notify_party.name` | +12.5 |
| seen | `notify_party.address` | +12.5 |
| seen | `notify_party.country` | +12.5 |
| seen | `line.unit` | -1.5 |

## 2 worst failures (of 2 documents with any error)

### inv_00002 (compact, buyer reference, extract ok)

0 critical and 3 other fields not correct. Lines: 10 true, 10 predicted, 10 matched.

| Field | Outcome | Expected | Predicted |
| --- | --- | --- | --- |
| `buyer.name` | wrong | Omeir-Luqmaan | As consignee |
| `buyer.address` | missed | Plot 93, Block C, Multan 81714 | null |
| `buyer.country` | missed | PK | null |

### inv_00025 (modern, buyer repeated, extract ok)

0 critical and 2 other fields not correct. Lines: 9 true, 9 predicted, 9 matched.

| Field | Outcome | Expected | Predicted |
| --- | --- | --- | --- |
| `line.unit` | wrong | MT | MET |
| `line.unit` | wrong | SET | SE |
