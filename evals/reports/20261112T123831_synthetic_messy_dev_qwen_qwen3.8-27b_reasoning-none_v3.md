# Eval run 20261112T123831_synthetic_messy_dev_qwen_qwen3.8-27b_reasoning-none_v3

- Dataset: `synthetic_messy`, split `dev`
- Model: `qwen/qwen3.8-27b`, reasoning `none`, prompt `v3`, schema `1.0`
- Git: `75993091da (uncommitted changes)`, run at 2026-11-12 12:38 UTC
- Documents scored: 2 of 2; LLM calls made: 0, from cache: 2

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
| Keys repaired | 0 | 0 |
| Keys ignored | 0 | 0 |
| Numbers normalised | 13 | 0 |
| Numbers ambiguous | 0 | 0 |
| Buyer references resolved by rule | 0 | 0 |
| Unit checks failed | 0 | 0 |
| Cost per document | $0.00000 | — |
| Latency p50 | 2.1 s | — |
| Latency p95 | 8.4 s | — |

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
| classic | 1 | 100.0% | 100.0% | 100.0% |

## Buyer by how it is printed

`reference`: the buyer block says e.g. "Same as consignee" and the truth is the consignee's details. `repeated`: the same company printed in full. `distinct`: a different company.

| Buyer form | Field | Seen | Unseen |
| --- | --- | --- | --- |
| repeated | `buyer.name` | 100.0% (2/2) | — (0) |
| repeated | `buyer.address` | 100.0% (2/2) | — (0) |
| repeated | `buyer.country` | 100.0% (2/2) | — (0) |

## Change since the previous run

Previous: `20261112T122001_synthetic_messy_dev_qwen_qwen3.8-27b_reasoning-none_v3` (model `qwen/qwen3.8-27b`, prompt `v3`, 2/2 scored). Points of change:

| Metric | Seen | Unseen |
| --- | --- | --- |
| Critical-field accuracy | +0.0 | — |
| All critical right | +0.0 | — |
| Line F1 | +0.0 | — |

## 1 worst failures (of 1 documents with any error)

### inv_00003_messy (airy, buyer repeated, extract ok)

1 critical and 1 other fields not correct. Lines: 12 true, 12 predicted, 12 matched.

| Field | Outcome | Expected | Predicted |
| --- | --- | --- | --- |
| `incoterm` | wrong | CIP | CIF |
