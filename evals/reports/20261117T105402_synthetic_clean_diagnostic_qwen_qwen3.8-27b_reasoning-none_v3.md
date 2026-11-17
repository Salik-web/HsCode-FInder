# Eval run 20261117T105402_synthetic_clean_diagnostic_qwen_qwen3.8-27b_reasoning-none_v3

- Dataset: `synthetic_clean`, split `diagnostic`
- Model: `qwen/qwen3.8-27b`, reasoning `none`, prompt `v3`, schema `1.0`
- Git: `1eafdcff93 (uncommitted changes)`, run at 2026-11-17 10:54 UTC
- Documents scored: 10 of 10; LLM calls made: 0, from cache: 10

## Headline (seen and unseen layouts are never blended)

| Metric | Seen | Unseen |
| --- | --- | --- |
| Documents scored | 0 | 10 |
| Critical-field accuracy | — | 100.0% |
| All critical fields right | — | 100.0% |
| Line precision | — | 100.0% |
| Line recall | — | 100.0% |
| Line F1 | — | 100.0% |
| needs_review rate | — | 0.0% |
| Failed rate | — | 0.0% |
| First-try valid JSON | — (0/0) | 100.0% (10/10) |
| Retry rate | — (0/0) | 0.0% (0/10) |
| Keys repaired | 0 | 0 |
| Keys ignored | 0 | 0 |
| Numbers normalised | 0 | 0 |
| Numbers ambiguous | 0 | 0 |
| Buyer references resolved by rule | 0 | 2 |
| Unit checks failed | 0 | 0 |
| Cost per document | — | $0.00000 |
| Model call time p50 | — | 1.6 s (n=10) |
| Model call time p95 (target < 30 s) | — | 2.5 s (n=10) |
| Retry backoff wait p95 | — | 30.7 s (n=10) |
| Throttle wait p95 | — | 60.0 s (n=10) |
| Call + backoff p50 (all documents) | — | 1.7 s |
| Call + backoff p95 (all documents) | — | 31.8 s |
| Guardrail alarms on wrong / correct values | — | 0 / 0 |
| Fully correct documents flagged (false alarms) | — | 0.0% (0/9) |

## Confidence calibration

Confidence comes from deterministic signals (app/pipeline/confidence.py). Below 0.5 a field is needs_review; a failed check is failed_check. Both go to a person.

### Unseen layouts

| Confidence | Fields | Correct | Accuracy |
| --- | --- | --- | --- |
| [0.0, 0.3) | 0 | 0 | — |
| [0.3, 0.5) | 1 | 0 | 0.0% |
| [0.5, 0.7) | 0 | 0 | — |
| [0.7, 0.9) | 88 | 88 | 100.0% |
| [0.9, 1.0] | 509 | 508 | 99.8% |

- Error recall: 50.0% (1 of 2 wrong fields sent to review: 1 needs_review, 0 failed_check)
- Review load: 0.0% (0 of 596 correct fields sent to review)
- Not in these counts: 0 missed lines (no field to review), 0 documents with no extracted invoice

## Per-field accuracy (correct/total; line fields on matched lines only)

| Field | Seen | Unseen | Seen missed / hallucinated |
| --- | --- | --- | --- |
| `invoice_number` | — (0) | 100.0% (10/10) | — |
| `invoice_date` | — (0) | 100.0% (10/10) | — |
| `incoterm` | — (0) | 100.0% (10/10) | — |
| `currency` | — (0) | 100.0% (10/10) | — |
| `total_amount` | — (0) | 100.0% (10/10) | — |
| `gross_weight_kg` | — (0) | 100.0% (10/10) | — |
| `net_weight_kg` | — (0) | 100.0% (10/10) | — |
| `shipper.name` | — (0) | 100.0% (10/10) | — |
| `shipper.address` | — (0) | 100.0% (10/10) | — |
| `shipper.country` | — (0) | 100.0% (10/10) | — |
| `buyer.name` | — (0) | 100.0% (10/10) | — |
| `buyer.address` | — (0) | 100.0% (10/10) | — |
| `buyer.country` | — (0) | 100.0% (10/10) | — |
| `consignee.name` | — (0) | 100.0% (10/10) | — |
| `consignee.address` | — (0) | 100.0% (10/10) | — |
| `consignee.country` | — (0) | 100.0% (10/10) | — |
| `notify_party.name` | — (0) | 100.0% (10/10) | — |
| `notify_party.address` | — (0) | 100.0% (10/10) | — |
| `notify_party.country` | — (0) | 100.0% (10/10) | — |
| `line.description` | — (0) | 98.5% (67/68) | — |
| `line.quantity` | — (0) | 100.0% (68/68) | — |
| `line.unit` | — (0) | 98.5% (67/68) | — |
| `line.unit_price` | — (0) | 100.0% (68/68) | — |
| `line.amount` | — (0) | 100.0% (68/68) | — |
| `line.hs_code_on_doc` | — (0) | 100.0% (68/68) | — |

## By template

| Template | Documents | Critical-field accuracy | All critical right | Line F1 |
| --- | --- | --- | --- | --- |
| ledger | 10 | 100.0% | 100.0% | 100.0% |

## Buyer by how it is printed

`reference`: the buyer block says e.g. "Same as consignee" and the truth is the consignee's details. `repeated`: the same company printed in full. `distinct`: a different company.

| Buyer form | Field | Seen | Unseen |
| --- | --- | --- | --- |
| reference | `buyer.name` | — (0) | 100.0% (2/2) |
| reference | `buyer.address` | — (0) | 100.0% (2/2) |
| reference | `buyer.country` | — (0) | 100.0% (2/2) |
| repeated | `buyer.name` | — (0) | 100.0% (3/3) |
| repeated | `buyer.address` | — (0) | 100.0% (3/3) |
| repeated | `buyer.country` | — (0) | 100.0% (3/3) |
| distinct | `buyer.name` | — (0) | 100.0% (5/5) |
| distinct | `buyer.address` | — (0) | 100.0% (5/5) |
| distinct | `buyer.country` | — (0) | 100.0% (5/5) |

## Change since the previous run

No earlier run on this dataset and split.

## 1 worst failures (of 1 documents with any error)

### diag_00007 (ledger, buyer repeated, extract ok)

0 critical and 2 other fields not correct. Lines: 10 true, 10 predicted, 10 matched.

| Field | Outcome | Expected | Predicted |
| --- | --- | --- | --- |
| `line.description` | wrong | Hot-rolled steel coils | Hot-rolled钢 coils |
| `line.unit` | wrong | MT | CTN |
