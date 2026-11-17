# Eval run 20261117T104316_synthetic_messy_test_unseen_layout_qwen_qwen3.8-27b_reasoning-none_v3

- Dataset: `synthetic_messy`, split `test_unseen_layout`
- Model: `qwen/qwen3.8-27b`, reasoning `none`, prompt `v3`, schema `1.0`
- Git: `1eafdcff93`, run at 2026-11-17 10:43 UTC
- Documents scored: 7 of 7; LLM calls made: 0, from cache: 8

## Headline (seen and unseen layouts are never blended)

| Metric | Seen | Unseen |
| --- | --- | --- |
| Documents scored | 0 | 7 |
| Critical-field accuracy | — | 98.8% |
| All critical fields right | — | 85.7% |
| Line precision | — | 100.0% |
| Line recall | — | 100.0% |
| Line F1 | — | 100.0% |
| needs_review rate | — | 0.0% |
| Failed rate | — | 0.0% |
| First-try valid JSON | — (0/0) | 85.7% (6/7) |
| Retry rate | — (0/0) | 14.3% (1/7) |
| Keys repaired | 0 | 0 |
| Keys ignored | 0 | 0 |
| Numbers normalised | 0 | 7 |
| Numbers ambiguous | 0 | 0 |
| Buyer references resolved by rule | 0 | 0 |
| Unit checks failed | 0 | 0 |
| Cost per document | — | unknown |
| Model call time p50 | — | not recorded |
| Model call time p95 (target < 30 s) | — | not recorded |
| Retry backoff wait p95 | — | not recorded |
| Throttle wait p95 | — | not recorded |
| Call + backoff p50 (all documents) | — | 1.7 s |
| Call + backoff p95 (all documents) | — | 9.5 s |
| Guardrail alarms on wrong / correct values | — | 0 / 0 |
| Fully correct documents flagged (false alarms) | — | 0.0% (0/5) |

## Confidence calibration

Confidence comes from deterministic signals (app/pipeline/confidence.py). Below 0.5 a field is needs_review; a failed check is failed_check. Both go to a person.

### Unseen layouts

| Confidence | Fields | Correct | Accuracy |
| --- | --- | --- | --- |
| [0.0, 0.3) | 0 | 0 | — |
| [0.3, 0.5) | 0 | 0 | — |
| [0.5, 0.7) | 397 | 395 | 99.5% |
| [0.7, 0.9) | 0 | 0 | — |
| [0.9, 1.0] | 0 | 0 | — |

- Error recall: 50.0% (1 of 2 wrong fields sent to review: 1 needs_review, 0 failed_check)
- Review load: 21.5% (85 of 395 correct fields sent to review)
- Not in these counts: 0 missed lines (no field to review), 0 documents with no extracted invoice

## Per-field accuracy (correct/total; line fields on matched lines only)

| Field | Seen | Unseen | Seen missed / hallucinated |
| --- | --- | --- | --- |
| `invoice_number` | — (0) | 100.0% (7/7) | — |
| `invoice_date` | — (0) | 100.0% (7/7) | — |
| `incoterm` | — (0) | 100.0% (7/7) | — |
| `currency` | — (0) | 100.0% (7/7) | — |
| `total_amount` | — (0) | 100.0% (7/7) | — |
| `gross_weight_kg` | — (0) | 100.0% (7/7) | — |
| `net_weight_kg` | — (0) | 100.0% (7/7) | — |
| `shipper.name` | — (0) | 100.0% (7/7) | — |
| `shipper.address` | — (0) | 85.7% (6/7) | — |
| `shipper.country` | — (0) | 100.0% (7/7) | — |
| `buyer.name` | — (0) | 100.0% (7/7) | — |
| `buyer.address` | — (0) | 100.0% (7/7) | — |
| `buyer.country` | — (0) | 100.0% (7/7) | — |
| `consignee.name` | — (0) | 85.7% (6/7) | — |
| `consignee.address` | — (0) | 100.0% (7/7) | — |
| `consignee.country` | — (0) | 100.0% (7/7) | — |
| `notify_party.name` | — (0) | 100.0% (7/7) | — |
| `notify_party.address` | — (0) | 100.0% (7/7) | — |
| `notify_party.country` | — (0) | 100.0% (7/7) | — |
| `line.description` | — (0) | 100.0% (44/44) | — |
| `line.quantity` | — (0) | 100.0% (44/44) | — |
| `line.unit` | — (0) | 100.0% (44/44) | — |
| `line.unit_price` | — (0) | 100.0% (44/44) | — |
| `line.amount` | — (0) | 100.0% (44/44) | — |
| `line.hs_code_on_doc` | — (0) | 100.0% (44/44) | — |

## By template

| Template | Documents | Critical-field accuracy | All critical right | Line F1 |
| --- | --- | --- | --- | --- |
| ledger | 3 | 96.4% | 66.7% | 100.0% |
| sidebar | 4 | 100.0% | 100.0% | 100.0% |

## Buyer by how it is printed

`reference`: the buyer block says e.g. "Same as consignee" and the truth is the consignee's details. `repeated`: the same company printed in full. `distinct`: a different company.

| Buyer form | Field | Seen | Unseen |
| --- | --- | --- | --- |
| reference | `buyer.name` | — (0) | 100.0% (1/1) |
| reference | `buyer.address` | — (0) | 100.0% (1/1) |
| reference | `buyer.country` | — (0) | 100.0% (1/1) |
| repeated | `buyer.name` | — (0) | 100.0% (5/5) |
| repeated | `buyer.address` | — (0) | 100.0% (5/5) |
| repeated | `buyer.country` | — (0) | 100.0% (5/5) |
| distinct | `buyer.name` | — (0) | 100.0% (1/1) |
| distinct | `buyer.address` | — (0) | 100.0% (1/1) |
| distinct | `buyer.country` | — (0) | 100.0% (1/1) |

## Change since the previous run

No earlier run on this dataset and split.

## Failures

Per-document details are shown for dev, diagnostic only, so this split can't be used for tuning.
