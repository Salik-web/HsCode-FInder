# Eval run 20261123T111649_synthetic_messy_test_unseen_layout_qwen_qwen3.8-27b_reasoning-none_v3

- Dataset: `synthetic_messy`, split `test_unseen_layout`
- Model: `qwen/qwen3.8-27b`, reasoning `none`, prompt `v3`, schema `1.0`
- Git: `e5495aeab0`, run at 2026-11-23 11:16 UTC
- Documents scored: 4 of 4; LLM calls made: 0, from cache: 4

## Headline (seen and unseen layouts are never blended)

| Metric | Seen | Unseen |
| --- | --- | --- |
| Documents scored | 0 | 4 |
| Critical-field accuracy | — | 98.4% |
| All critical fields right | — | 75.0% |
| Line precision | — | 100.0% |
| Line recall | — | 100.0% |
| Line F1 | — | 100.0% |
| needs_review rate | — | 0.0% |
| Failed rate | — | 0.0% |
| First-try valid JSON | — (0/0) | 100.0% (4/4) |
| Retry rate | — (0/0) | 0.0% (0/4) |
| Keys repaired | 0 | 0 |
| Keys ignored | 0 | 0 |
| Numbers normalised | 0 | 0 |
| Numbers ambiguous | 0 | 0 |
| Buyer references resolved by rule | 0 | 1 |
| Unit checks failed | 0 | 0 |
| Cost per document | — | $0.00000 |
| Model call time p50 | — | 1.8 s (n=4) |
| Model call time p95 (target < 30 s) | — | 2.1 s (n=4) |
| Retry backoff wait p95 | — | 0.4 s (n=4) |
| Throttle wait p95 | — | 60.0 s (n=4) |
| Call + backoff p50 (all documents) | — | 1.9 s |
| Call + backoff p95 (all documents) | — | 2.1 s |
| Guardrail alarms on wrong / correct values | — | 0 / 0 |
| Fully correct documents flagged (false alarms) | — | 0.0% (0/2) |

## Confidence calibration

Confidence comes from deterministic signals (app/pipeline/confidence.py). Below 0.5 a field is needs_review; a failed check is failed_check. Both go to a person.

### Unseen layouts

| Confidence | Fields | Correct | Accuracy |
| --- | --- | --- | --- |
| [0.0, 0.3) | 0 | 0 | — |
| [0.3, 0.5) | 0 | 0 | — |
| [0.5, 0.7) | 304 | 299 | 98.4% |
| [0.7, 0.9) | 0 | 0 | — |
| [0.9, 1.0] | 0 | 0 | — |

- Error recall: 20.0% (1 of 5 wrong fields sent to review: 1 needs_review, 0 failed_check)
- Review load: 83.9% (251 of 299 correct fields sent to review)
- Not in these counts: 0 missed lines (no field to review), 0 documents with no extracted invoice

## Per-field accuracy (correct/total; line fields on matched lines only)

| Field | Seen | Unseen | Seen missed / hallucinated |
| --- | --- | --- | --- |
| `invoice_number` | — (0) | 100.0% (4/4) | — |
| `invoice_date` | — (0) | 100.0% (4/4) | — |
| `incoterm` | — (0) | 100.0% (4/4) | — |
| `currency` | — (0) | 100.0% (4/4) | — |
| `total_amount` | — (0) | 100.0% (4/4) | — |
| `gross_weight_kg` | — (0) | 100.0% (4/4) | — |
| `net_weight_kg` | — (0) | 100.0% (4/4) | — |
| `shipper.name` | — (0) | 100.0% (4/4) | — |
| `shipper.address` | — (0) | 100.0% (4/4) | — |
| `shipper.country` | — (0) | 100.0% (4/4) | — |
| `buyer.name` | — (0) | 75.0% (3/4) | — |
| `buyer.address` | — (0) | 50.0% (2/4) | — |
| `buyer.country` | — (0) | 75.0% (3/4) | — |
| `consignee.name` | — (0) | 100.0% (4/4) | — |
| `consignee.address` | — (0) | 75.0% (3/4) | — |
| `consignee.country` | — (0) | 100.0% (4/4) | — |
| `notify_party.name` | — (0) | 100.0% (4/4) | — |
| `notify_party.address` | — (0) | 100.0% (4/4) | — |
| `notify_party.country` | — (0) | 100.0% (4/4) | — |
| `line.description` | — (0) | 100.0% (38/38) | — |
| `line.quantity` | — (0) | 100.0% (38/38) | — |
| `line.unit` | — (0) | 100.0% (38/38) | — |
| `line.unit_price` | — (0) | 100.0% (38/38) | — |
| `line.amount` | — (0) | 100.0% (38/38) | — |
| `line.hs_code_on_doc` | — (0) | 100.0% (38/38) | — |

## By template

| Template | Documents | Critical-field accuracy | All critical right | Line F1 |
| --- | --- | --- | --- | --- |
| manifest | 4 | 98.4% | 75.0% | 100.0% |

## Buyer by how it is printed

`reference`: the buyer block says e.g. "Same as consignee" and the truth is the consignee's details. `repeated`: the same company printed in full. `distinct`: a different company.

| Buyer form | Field | Seen | Unseen |
| --- | --- | --- | --- |
| reference | `buyer.name` | — (0) | 50.0% (1/2) |
| reference | `buyer.address` | — (0) | 50.0% (1/2) |
| reference | `buyer.country` | — (0) | 50.0% (1/2) |
| repeated | `buyer.name` | — (0) | 100.0% (1/1) |
| repeated | `buyer.address` | — (0) | 100.0% (1/1) |
| repeated | `buyer.country` | — (0) | 100.0% (1/1) |
| distinct | `buyer.name` | — (0) | 100.0% (1/1) |
| distinct | `buyer.address` | — (0) | 0.0% (0/1) |
| distinct | `buyer.country` | — (0) | 100.0% (1/1) |

## Change since the previous run

Previous: `20261117T104316_synthetic_messy_test_unseen_layout_qwen_qwen3.8-27b_reasoning-none_v3` (model `qwen/qwen3.8-27b`, prompt `v3`, 7/7 scored). Points of change:

| Metric | Seen | Unseen |
| --- | --- | --- |
| Critical-field accuracy | — | -0.5 |
| All critical right | — | -10.7 |
| Line F1 | — | +0.0 |

| Group | Field | Points |
| --- | --- | --- |
| unseen | `shipper.address` | +14.3 |
| unseen | `buyer.name` | -25.0 |
| unseen | `buyer.address` | -50.0 |
| unseen | `buyer.country` | -25.0 |
| unseen | `consignee.name` | +14.3 |
| unseen | `consignee.address` | -25.0 |

## Failures

Per-document details are shown for dev, diagnostic only, so this split can't be used for tuning.
