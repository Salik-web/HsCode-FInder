# Eval run 20261117T113325_synthetic_messy_dev_qwen_qwen3.8-27b_reasoning-none_v3

- Dataset: `synthetic_messy`, split `dev`
- Model: `qwen/qwen3.8-27b`, reasoning `none`, prompt `v3`, schema `1.0`
- Git: `c74444aeb4`, run at 2026-11-17 11:33 UTC
- Documents scored: 16 of 16; LLM calls made: 0, from cache: 17

## Headline (seen and unseen layouts are never blended)

| Metric | Seen | Unseen |
| --- | --- | --- |
| Documents scored | 16 | 0 |
| Critical-field accuracy | 94.5% | — |
| All critical fields right | 62.5% | — |
| Line precision | 100.0% | — |
| Line recall | 100.0% | — |
| Line F1 | 100.0% | — |
| needs_review rate | 0.0% | — |
| Failed rate | 0.0% | — |
| First-try valid JSON | 93.8% (15/16) | — (0/0) |
| Retry rate | 6.2% (1/16) | — (0/0) |
| Keys repaired | 0 | 0 |
| Keys ignored | 0 | 0 |
| Numbers normalised | 22 | 0 |
| Numbers ambiguous | 0 | 0 |
| Buyer references resolved by rule | 1 | 0 |
| Unit checks failed | 0 | 0 |
| Cost per document | $0.00000 | — |
| Model call time p50 | 1.8 s (n=14) | — |
| Model call time p95 (target < 30 s) | 3.0 s (n=14) | — |
| Retry backoff wait p95 | 0.2 s (n=14) | — |
| Throttle wait p95 | 120.0 s (n=14) | — |
| Call + backoff p50 (all documents) | 1.9 s | — |
| Call + backoff p95 (all documents) | 8.4 s | — |
| Guardrail alarms on wrong / correct values | 2 / 3 | — |
| Fully correct documents flagged (false alarms) | 0.0% (0/8) | — |

## Confidence calibration

Confidence comes from deterministic signals (app/pipeline/confidence.py). Below 0.5 a field is needs_review; a failed check is failed_check. Both go to a person.

### Seen layouts

| Confidence | Fields | Correct | Accuracy |
| --- | --- | --- | --- |
| [0.0, 0.3) | 5 | 3 | 60.0% |
| [0.3, 0.5) | 3 | 3 | 100.0% |
| [0.5, 0.7) | 1142 | 1103 | 96.6% |
| [0.7, 0.9) | 0 | 0 | — |
| [0.9, 1.0] | 0 | 0 | — |

- Error recall: 31.7% (13 of 41 wrong fields sent to review: 11 needs_review, 2 failed_check)
- Review load: 20.4% (226 of 1109 correct fields sent to review)
- Not in these counts: 0 missed lines (no field to review), 0 documents with no extracted invoice

## Per-field accuracy (correct/total; line fields on matched lines only)

| Field | Seen | Unseen | Seen missed / hallucinated |
| --- | --- | --- | --- |
| `invoice_number` | 100.0% (16/16) | — (0) | 0 / 0 |
| `invoice_date` | 100.0% (16/16) | — (0) | 0 / 0 |
| `incoterm` | 93.8% (15/16) | — (0) | 0 / 0 |
| `currency` | 100.0% (16/16) | — (0) | 0 / 0 |
| `total_amount` | 100.0% (16/16) | — (0) | 0 / 0 |
| `gross_weight_kg` | 100.0% (16/16) | — (0) | 0 / 0 |
| `net_weight_kg` | 100.0% (16/16) | — (0) | 0 / 0 |
| `shipper.name` | 93.8% (15/16) | — (0) | 0 / 0 |
| `shipper.address` | 100.0% (16/16) | — (0) | 0 / 0 |
| `shipper.country` | 100.0% (16/16) | — (0) | 0 / 0 |
| `buyer.name` | 87.5% (14/16) | — (0) | 0 / 0 |
| `buyer.address` | 100.0% (16/16) | — (0) | 0 / 0 |
| `buyer.country` | 100.0% (16/16) | — (0) | 0 / 0 |
| `consignee.name` | 87.5% (14/16) | — (0) | 0 / 0 |
| `consignee.address` | 100.0% (16/16) | — (0) | 0 / 0 |
| `consignee.country` | 100.0% (16/16) | — (0) | 0 / 0 |
| `notify_party.name` | 93.8% (15/16) | — (0) | 0 / 0 |
| `notify_party.address` | 100.0% (16/16) | — (0) | 0 / 0 |
| `notify_party.country` | 100.0% (16/16) | — (0) | 0 / 0 |
| `line.description` | 100.0% (141/141) | — (0) | 0 / 0 |
| `line.quantity` | 95.7% (135/141) | — (0) | 1 / 0 |
| `line.unit` | 96.5% (136/141) | — (0) | 1 / 0 |
| `line.unit_price` | 94.3% (133/141) | — (0) | 1 / 0 |
| `line.amount` | 94.3% (133/141) | — (0) | 1 / 0 |
| `line.hs_code_on_doc` | 95.0% (134/141) | — (0) | 1 / 0 |

## By template

| Template | Documents | Critical-field accuracy | All critical right | Line F1 |
| --- | --- | --- | --- | --- |
| airy | 3 | 93.6% | 33.3% | 100.0% |
| boxed | 3 | 100.0% | 100.0% | 100.0% |
| classic | 4 | 96.9% | 75.0% | 100.0% |
| compact | 3 | 85.1% | 33.3% | 100.0% |
| modern | 2 | 96.9% | 50.0% | 100.0% |
| summary | 1 | 100.0% | 100.0% | 100.0% |

## Buyer by how it is printed

`reference`: the buyer block says e.g. "Same as consignee" and the truth is the consignee's details. `repeated`: the same company printed in full. `distinct`: a different company.

| Buyer form | Field | Seen | Unseen |
| --- | --- | --- | --- |
| reference | `buyer.name` | 66.7% (2/3) | — (0) |
| reference | `buyer.address` | 100.0% (3/3) | — (0) |
| reference | `buyer.country` | 100.0% (3/3) | — (0) |
| repeated | `buyer.name` | 90.9% (10/11) | — (0) |
| repeated | `buyer.address` | 100.0% (11/11) | — (0) |
| repeated | `buyer.country` | 100.0% (11/11) | — (0) |
| distinct | `buyer.name` | 100.0% (2/2) | — (0) |
| distinct | `buyer.address` | 100.0% (2/2) | — (0) |
| distinct | `buyer.country` | 100.0% (2/2) | — (0) |

## Change since the previous run

Previous: `20261112T123831_synthetic_messy_dev_qwen_qwen3.8-27b_reasoning-none_v3` (model `qwen/qwen3.8-27b`, prompt `v3`, 2/2 scored). Points of change:

| Metric | Seen | Unseen |
| --- | --- | --- |
| Critical-field accuracy | -2.6 | — |
| All critical right | +12.5 | — |
| Line F1 | +0.0 | — |

| Group | Field | Points |
| --- | --- | --- |
| seen | `incoterm` | +43.8 |
| seen | `shipper.name` | -6.2 |
| seen | `buyer.name` | -12.5 |
| seen | `consignee.name` | -12.5 |
| seen | `notify_party.name` | -6.2 |
| seen | `line.quantity` | -4.3 |
| seen | `line.unit` | -3.5 |
| seen | `line.unit_price` | -5.7 |
| seen | `line.amount` | -5.7 |
| seen | `line.hs_code_on_doc` | -5.0 |

## 8 worst failures (of 8 documents with any error)

### inv_00034_messy (compact, buyer repeated, extract ok)

5 critical and 26 other fields not correct. Lines: 13 true, 13 predicted, 13 matched.

- Check failed: line_items[7].amount: quantity 435 x unit price 214.80 = 93438.00, but the amount is 93477.15 (off by 39.15) (false alarm, the value is right)

| Field | Outcome | Expected | Predicted |
| --- | --- | --- | --- |
| `line.hs_code_on_doc` | wrong | 6302.2100 | 6212.2100 |
| `line.hs_code_on_doc` | wrong | 2523.2900 | 9401.4000 |
| `line.hs_code_on_doc` | wrong | 9405.4200 | 9404.9000 |
| `line.hs_code_on_doc` | wrong | 0902.4000 | 0902.1000 |
| `line.unit_price` | wrong | 214.89 | 214.80 |
| `line.quantity` | wrong | 873.99 | 66 |
| `line.unit` | wrong | KG | PCS |
| `line.unit_price` | wrong | 199.17 | 353.28 |
| `line.amount` | wrong | 174072.59 | 23316.48 |
| `line.quantity` | wrong | 66 | 881.15 |
| `line.unit` | wrong | PCS | KG |
| `line.unit_price` | wrong | 353.28 | 276.49 |
| `line.amount` | wrong | 23316.48 | 243629.16 |
| `line.quantity` | wrong | 881.15 | 681.17 |
| `line.unit_price` | wrong | 276.49 | 144.99 |
| `line.amount` | wrong | 243629.16 | 98762.84 |
| `line.hs_code_on_doc` | wrong | 1006.3010 | 1006.3020 |
| `line.quantity` | wrong | 681.17 | 31.21 |
| `line.unit` | wrong | KG | MTR |
| `line.unit_price` | wrong | 144.99 | 369.52 |
| `line.amount` | wrong | 98762.84 | 11532.72 |
| `line.quantity` | missed | 31.21 | null |
| `line.unit` | missed | MTR | null |
| `line.unit_price` | missed | 369.52 | null |
| `line.amount` | missed | 11532.72 | null |
| `line.hs_code_on_doc` | missed | 5209.4200 | null |

### inv_00026_messy (compact, buyer distinct, extract ok)

2 critical and 7 other fields not correct. Lines: 12 true, 12 predicted, 12 matched.

- Check failed: line_items[8].amount: quantity 27 x unit price 249.44 = 6734.88, but the amount is 107259.20 (off by 100524.32) (value is wrong)
- Check failed: total_amount: line amounts add up to 1816158.18, but the total is 1489322.85 (off by 326835.33; rounding allows 0.065) (false alarm, the value is right)

| Field | Outcome | Expected | Predicted |
| --- | --- | --- | --- |
| `line.hs_code_on_doc` | wrong | 9018.9010 | 9018.3910 |
| `line.unit_price` | wrong | 62.45 | 249.44 |
| `line.amount` | wrong | 1686.15 | 107259.20 |
| `line.quantity` | wrong | 430 | 882.15 |
| `line.unit` | wrong | PCS | KG |
| `line.unit_price` | wrong | 249.44 | 372.41 |
| `line.amount` | wrong | 107259.20 | 328521.48 |

### inv_00011_messy (airy, buyer reference, extract ok)

2 critical and 2 other fields not correct. Lines: 6 true, 6 predicted, 6 matched.

| Field | Outcome | Expected | Predicted |
| --- | --- | --- | --- |
| `buyer.name` | wrong | Sabir-Ghanee | Sahir-Ghaneer |
| `consignee.name` | wrong | Sabir-Ghanee | Sahir-Ghaneer |

### inv_00080_messy (classic, buyer repeated, extract ok)

2 critical and 2 other fields not correct. Lines: 11 true, 11 predicted, 11 matched.

| Field | Outcome | Expected | Predicted |
| --- | --- | --- | --- |
| `buyer.name` | wrong | Umair PLC | Umais PLC |
| `consignee.name` | wrong | Umair PLC | Umais PLC |

### inv_00003_messy (airy, buyer repeated, extract ok)

1 critical and 1 other fields not correct. Lines: 12 true, 12 predicted, 12 matched.

| Field | Outcome | Expected | Predicted |
| --- | --- | --- | --- |
| `incoterm` | wrong | CIP | CIF |

### inv_00049_messy (modern, buyer repeated, extract ok)

1 critical and 1 other fields not correct. Lines: 8 true, 8 predicted, 8 matched.

- Check failed: line_items[3].amount: quantity 288.75 x unit price 48.10 = 13888.8750, but the amount is 13880.88 (off by 7.9950) (value is wrong)
- Check failed: total_amount: line amounts add up to 571862.38, but the total is 571870.38 (off by 8.00; rounding allows 0.045) (false alarm, the value is right)

| Field | Outcome | Expected | Predicted |
| --- | --- | --- | --- |
| `line.amount` | wrong | 13888.88 | 13880.88 |

### inv_00045_messy (boxed, buyer reference, extract ok)

0 critical and 1 other fields not correct. Lines: 7 true, 7 predicted, 7 matched.

- Rule: buyer resolved_by_rule: the buyer block refers to the consignee

| Field | Outcome | Expected | Predicted |
| --- | --- | --- | --- |
| `notify_party.name` | wrong | Saal PLC | Saud PLC |

### inv_00095_messy (summary, buyer repeated, extract ok)

0 critical and 1 other fields not correct. Lines: 8 true, 8 predicted, 8 matched.

| Field | Outcome | Expected | Predicted |
| --- | --- | --- | --- |
| `shipper.name` | wrong | Sakarya Dumanlı Ltd. | Sakarya Dumanki Ltd. |
