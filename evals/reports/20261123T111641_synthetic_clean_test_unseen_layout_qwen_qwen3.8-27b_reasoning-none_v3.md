# Eval run 20261123T111641_synthetic_clean_test_unseen_layout_qwen_qwen3.8-27b_reasoning-none_v3

- Dataset: `synthetic_clean`, split `test_unseen_layout`
- Model: `qwen/qwen3.8-27b`, reasoning `none`, prompt `v3`, schema `1.0`
- Git: `e5495aeab0`, run at 2026-11-23 11:16 UTC
- Documents scored: 12 of 12; LLM calls made: 0, from cache: 12

## Headline (seen and unseen layouts are never blended)

| Metric | Seen | Unseen |
| --- | --- | --- |
| Documents scored | 0 | 12 |
| Critical-field accuracy | — | 100.0% |
| All critical fields right | — | 100.0% |
| Line precision | — | 100.0% |
| Line recall | — | 100.0% |
| Line F1 | — | 100.0% |
| needs_review rate | — | 0.0% |
| Failed rate | — | 0.0% |
| First-try valid JSON | — (0/0) | 100.0% (12/12) |
| Retry rate | — (0/0) | 0.0% (0/12) |
| Keys repaired | 0 | 0 |
| Keys ignored | 0 | 0 |
| Numbers normalised | 0 | 0 |
| Numbers ambiguous | 0 | 0 |
| Buyer references resolved by rule | 0 | 0 |
| Unit checks failed | 0 | 0 |
| Cost per document | — | $0.00000 |
| Model call time p50 | — | 1.9 s (n=12) |
| Model call time p95 (target < 30 s) | — | 2.7 s (n=12) |
| Retry backoff wait p95 | — | 0.2 s (n=12) |
| Throttle wait p95 | — | 60.0 s (n=12) |
| Call + backoff p50 (all documents) | — | 1.9 s |
| Call + backoff p95 (all documents) | — | 2.7 s |
| Guardrail alarms on wrong / correct values | — | 0 / 0 |
| Fully correct documents flagged (false alarms) | — | 0.0% (0/12) |

## Confidence calibration

Confidence comes from deterministic signals (app/pipeline/confidence.py). Below 0.5 a field is needs_review; a failed check is failed_check. Both go to a person.

### Unseen layouts

| Confidence | Fields | Correct | Accuracy |
| --- | --- | --- | --- |
| [0.0, 0.3) | 0 | 0 | — |
| [0.3, 0.5) | 0 | 0 | — |
| [0.5, 0.7) | 0 | 0 | — |
| [0.7, 0.9) | 102 | 102 | 100.0% |
| [0.9, 1.0] | 702 | 702 | 100.0% |

- Error recall: — (0 of 0 wrong fields sent to review: 0 needs_review, 0 failed_check)
- Review load: 0.0% (0 of 804 correct fields sent to review)
- Not in these counts: 0 missed lines (no field to review), 0 documents with no extracted invoice

## Per-field accuracy (correct/total; line fields on matched lines only)

| Field | Seen | Unseen | Seen missed / hallucinated |
| --- | --- | --- | --- |
| `invoice_number` | — (0) | 100.0% (12/12) | — |
| `invoice_date` | — (0) | 100.0% (12/12) | — |
| `incoterm` | — (0) | 100.0% (12/12) | — |
| `currency` | — (0) | 100.0% (12/12) | — |
| `total_amount` | — (0) | 100.0% (12/12) | — |
| `gross_weight_kg` | — (0) | 100.0% (12/12) | — |
| `net_weight_kg` | — (0) | 100.0% (12/12) | — |
| `shipper.name` | — (0) | 100.0% (12/12) | — |
| `shipper.address` | — (0) | 100.0% (12/12) | — |
| `shipper.country` | — (0) | 100.0% (12/12) | — |
| `buyer.name` | — (0) | 100.0% (12/12) | — |
| `buyer.address` | — (0) | 100.0% (12/12) | — |
| `buyer.country` | — (0) | 100.0% (12/12) | — |
| `consignee.name` | — (0) | 100.0% (12/12) | — |
| `consignee.address` | — (0) | 100.0% (12/12) | — |
| `consignee.country` | — (0) | 100.0% (12/12) | — |
| `notify_party.name` | — (0) | 100.0% (12/12) | — |
| `notify_party.address` | — (0) | 100.0% (12/12) | — |
| `notify_party.country` | — (0) | 100.0% (12/12) | — |
| `line.description` | — (0) | 100.0% (96/96) | — |
| `line.quantity` | — (0) | 100.0% (96/96) | — |
| `line.unit` | — (0) | 100.0% (96/96) | — |
| `line.unit_price` | — (0) | 100.0% (96/96) | — |
| `line.amount` | — (0) | 100.0% (96/96) | — |
| `line.hs_code_on_doc` | — (0) | 100.0% (96/96) | — |

## By template

| Template | Documents | Critical-field accuracy | All critical right | Line F1 |
| --- | --- | --- | --- | --- |
| manifest | 12 | 100.0% | 100.0% | 100.0% |

## Buyer by how it is printed

`reference`: the buyer block says e.g. "Same as consignee" and the truth is the consignee's details. `repeated`: the same company printed in full. `distinct`: a different company.

| Buyer form | Field | Seen | Unseen |
| --- | --- | --- | --- |
| reference | `buyer.name` | — (0) | 100.0% (3/3) |
| reference | `buyer.address` | — (0) | 100.0% (3/3) |
| reference | `buyer.country` | — (0) | 100.0% (3/3) |
| repeated | `buyer.name` | — (0) | 100.0% (7/7) |
| repeated | `buyer.address` | — (0) | 100.0% (7/7) |
| repeated | `buyer.country` | — (0) | 100.0% (7/7) |
| distinct | `buyer.name` | — (0) | 100.0% (2/2) |
| distinct | `buyer.address` | — (0) | 100.0% (2/2) |
| distinct | `buyer.country` | — (0) | 100.0% (2/2) |

## Change since the previous run

Previous: `20261116T100758_synthetic_clean_test_unseen_layout_qwen_qwen3.8-27b_reasoning-none_v3` (model `qwen/qwen3.8-27b`, prompt `v3`, 24/24 scored). Points of change:

| Metric | Seen | Unseen |
| --- | --- | --- |
| Critical-field accuracy | — | +0.9 |
| All critical right | — | +8.3 |
| Line F1 | — | +0.3 |

| Group | Field | Points |
| --- | --- | --- |
| unseen | `buyer.name` | +4.2 |
| unseen | `consignee.name` | +4.2 |
| unseen | `notify_party.name` | +4.2 |

## Failures

Per-document details are shown for dev, diagnostic only, so this split can't be used for tuning.
