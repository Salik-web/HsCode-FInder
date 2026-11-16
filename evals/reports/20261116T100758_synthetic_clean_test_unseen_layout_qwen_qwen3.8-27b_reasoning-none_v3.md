# Eval run 20261116T100758_synthetic_clean_test_unseen_layout_qwen_qwen3.8-27b_reasoning-none_v3

- Dataset: `synthetic_clean`, split `test_unseen_layout`
- Model: `qwen/qwen3.8-27b`, reasoning `none`, prompt `v3`, schema `1.0`
- Git: `9e28400e55 (uncommitted changes)`, run at 2026-11-16 10:07 UTC
- Documents scored: 24 of 24; LLM calls made: 0, from cache: 24

## Headline (seen and unseen layouts are never blended)

| Metric | Seen | Unseen |
| --- | --- | --- |
| Documents scored | 0 | 24 |
| Critical-field accuracy | — | 99.1% |
| All critical fields right | — | 91.7% |
| Line precision | — | 100.0% |
| Line recall | — | 99.4% |
| Line F1 | — | 99.7% |
| needs_review rate | — | 0.0% |
| Failed rate | — | 0.0% |
| First-try valid JSON | — (0/0) | 100.0% (24/24) |
| Retry rate | — (0/0) | 0.0% (0/24) |
| Keys repaired | 0 | 0 |
| Keys ignored | 0 | 0 |
| Numbers normalised | 0 | 0 |
| Numbers ambiguous | 0 | 0 |
| Buyer references resolved by rule | 0 | 1 |
| Unit checks failed | 0 | 0 |
| Cost per document | — | $0.00000 |
| Model call time p50 | — | not recorded |
| Model call time p95 (target < 30 s) | — | not recorded |
| Retry backoff wait p95 | — | not recorded |
| Throttle wait p95 | — | not recorded |
| Call + backoff p50 (all documents) | — | 1.7 s |
| Call + backoff p95 (all documents) | — | 2.4 s |
| Guardrail alarms on wrong / correct values | — | 0 / 1 |
| Fully correct documents flagged (false alarms) | — | 0.0% (0/22) |

## Confidence calibration

Confidence comes from deterministic signals (app/pipeline/confidence.py). Below 0.5 a field is needs_review; a failed check is failed_check. Both go to a person.

### Unseen layouts

| Confidence | Fields | Correct | Accuracy |
| --- | --- | --- | --- |
| [0.0, 0.3) | 1 | 1 | 100.0% |
| [0.3, 0.5) | 1 | 0 | 0.0% |
| [0.5, 0.7) | 0 | 0 | — |
| [0.7, 0.9) | 211 | 211 | 100.0% |
| [0.9, 1.0] | 1317 | 1315 | 99.8% |

- Error recall: 33.3% (1 of 3 wrong fields sent to review: 1 needs_review, 0 failed_check)
- Review load: 0.1% (1 of 1527 correct fields sent to review)
- Not in these counts: 1 missed lines (no field to review), 0 documents with no extracted invoice

## Per-field accuracy (correct/total; line fields on matched lines only)

| Field | Seen | Unseen | Seen missed / hallucinated |
| --- | --- | --- | --- |
| `invoice_number` | — (0) | 100.0% (24/24) | — |
| `invoice_date` | — (0) | 100.0% (24/24) | — |
| `incoterm` | — (0) | 100.0% (24/24) | — |
| `currency` | — (0) | 100.0% (24/24) | — |
| `total_amount` | — (0) | 100.0% (24/24) | — |
| `gross_weight_kg` | — (0) | 100.0% (24/24) | — |
| `net_weight_kg` | — (0) | 100.0% (24/24) | — |
| `shipper.name` | — (0) | 100.0% (24/24) | — |
| `shipper.address` | — (0) | 100.0% (24/24) | — |
| `shipper.country` | — (0) | 100.0% (24/24) | — |
| `buyer.name` | — (0) | 95.8% (23/24) | — |
| `buyer.address` | — (0) | 100.0% (24/24) | — |
| `buyer.country` | — (0) | 100.0% (24/24) | — |
| `consignee.name` | — (0) | 95.8% (23/24) | — |
| `consignee.address` | — (0) | 100.0% (24/24) | — |
| `consignee.country` | — (0) | 100.0% (24/24) | — |
| `notify_party.name` | — (0) | 95.8% (23/24) | — |
| `notify_party.address` | — (0) | 100.0% (24/24) | — |
| `notify_party.country` | — (0) | 100.0% (24/24) | — |
| `line.description` | — (0) | 100.0% (179/179) | — |
| `line.quantity` | — (0) | 100.0% (179/179) | — |
| `line.unit` | — (0) | 100.0% (179/179) | — |
| `line.unit_price` | — (0) | 100.0% (179/179) | — |
| `line.amount` | — (0) | 100.0% (179/179) | — |
| `line.hs_code_on_doc` | — (0) | 100.0% (179/179) | — |

## By template

| Template | Documents | Critical-field accuracy | All critical right | Line F1 |
| --- | --- | --- | --- | --- |
| ledger | 12 | 98.0% | 83.3% | 99.3% |
| sidebar | 12 | 100.0% | 100.0% | 100.0% |

## Buyer by how it is printed

`reference`: the buyer block says e.g. "Same as consignee" and the truth is the consignee's details. `repeated`: the same company printed in full. `distinct`: a different company.

| Buyer form | Field | Seen | Unseen |
| --- | --- | --- | --- |
| reference | `buyer.name` | — (0) | 100.0% (5/5) |
| reference | `buyer.address` | — (0) | 100.0% (5/5) |
| reference | `buyer.country` | — (0) | 100.0% (5/5) |
| repeated | `buyer.name` | — (0) | 91.7% (11/12) |
| repeated | `buyer.address` | — (0) | 100.0% (12/12) |
| repeated | `buyer.country` | — (0) | 100.0% (12/12) |
| distinct | `buyer.name` | — (0) | 100.0% (7/7) |
| distinct | `buyer.address` | — (0) | 100.0% (7/7) |
| distinct | `buyer.country` | — (0) | 100.0% (7/7) |

## Change since the previous run

Previous: `20261116T074314_synthetic_clean_test_unseen_layout_qwen_qwen3.8-27b_reasoning-none_v3` (model `qwen/qwen3.8-27b`, prompt `v3`, 24/24 scored). Points of change:

| Metric | Seen | Unseen |
| --- | --- | --- |
| Critical-field accuracy | — | +0.0 |
| All critical right | — | +0.0 |
| Line F1 | — | +0.0 |

## Failures

Per-document details are shown for dev, diagnostic only, so this split can't be used for tuning.
