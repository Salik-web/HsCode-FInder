# Eval changelog

One entry per eval run: date, prompt version, model, key metrics, what changed and why. Numbers are seen-layout dev unless marked otherwise; each carries its document count.

## 2026-11-17: dev messy, v3, complete (15 live calls)

- Model `qwen/qwen3.8-27b` (Groq, thinking off), prompt v3, with the row-scoped line check (photos have no text layer, so it changes nothing here). 14 new documents plus the 2 cached; 15 calls (one retry), no quota error. Report: `reports/20261117T113325_synthetic_messy_dev_qwen_qwen3.8-27b_reasoning-none_v3.md` (from cache at `c74444a`).
- 16/16 seen: critical-field accuracy **94.5%** (target ≥ 85%), all critical right **10/16**, line F1 100%, first-try valid 15/16, model call p95 3.0 s.
- Calibration: 41 wrong fields, error recall 13/41 (11 `needs_review`, 2 `failed_check`); review load 226/1109 (20.4%, the photo rule). The missed 28 are non-critical: quantities, units, unit prices and HS codes, plus two non-critical names.
- By template, all critical right: boxed 3/3, summary 1/1, classic 3/4, modern 1/2, airy 1/3, compact 1/3.
- Failure kinds (read before changing anything):
  - **Row shift on `compact` photos** (2 documents, 30 of the 41 wrong fields). `inv_00034_messy`: from row 9 on, each description carries the next row's quantity, unit, price and amount, the last row gets none, and one row's values are lost. `inv_00026_messy`: one row takes the next row's values. Each shifted line still multiplies out, so the line check can't see it. On `inv_00034` the total check didn't run either, because a line amount is `None` (`check_total` skips then, by design).
  - **Character misreads in names** (4 documents): `Sabir-Ghanee` -> `Sahir-Ghaneer`, `Umair` -> `Umais`, `Saal` -> `Saud`, `Dumanlı` -> `Dumanki`. Critical ones go to review by the photo rule.
  - **Digit misreads** (2): `13888.88` -> `13880.88` (caught by line math), `214.89` -> `214.80` (flagged as a line-math failure on the amount, which is right; the misread price is not flagged).
  - **Incoterm** CIP -> CIF on `inv_00003_messy` (known; already in the 2 cached).

## 2026-11-17: line values checked in their own row (re-scored from cache, 0 API calls)

- Salik's decision: a line's quantity, unit, unit price and amount must be found in the text of their own row, located by the line's description, not anywhere on the page (`app/pipeline/confidence.py`). The row is the fewest text lines (up to 3) printing the description word for word; a wrapped description cell adds the next text line for the row's remaining cells, unless another item's description starts there. A garbled description falls back to the lines sharing at least 80% of it (runs of 3+ characters); with no row, the four values are not found.
- Ground truth on all 110 clean PDFs (8 layouts, plus the diagnostic batch): 3,692/3,692 row-scoped values found in their own row, every description on exactly one row. Two `sidebar` descriptions wrap onto 2 text lines; before the wrap rule they cost 7 false alarms.
- The guardrail eval's stand-in text layer now prints each line item as one row, as layouts do.

| Set (cached v3) | Wrong | Error recall before -> after | Review load before -> after |
| --- | --- | --- | --- |
| Clean, seen (dev 27) | 6 | 6/6 -> 6/6 | 0/2043 -> 0/2043 |
| Clean, diagnostic ledger (10) | 2 | 1/2 -> **2/2** | 0/596 -> 0/596 |
| Clean, test_unseen_layout (24, frozen) | 3 | 1/3 -> 1/3 | 1/1527 -> 1/1527 |
| Messy, seen (dev 2) | 1 | 1/1 -> 1/1 | 38/199 -> 38/199 |
| Messy, test_unseen_layout (7, frozen) | 2 | 1/2 -> 1/2 | 85/395 -> 85/395 |
| **All** | 14 | **10/14 -> 11/14** | 124/4760 -> 124/4760 |

- Exactly one field changed bucket: `diag_00007`'s unit `CTN` (printed `MT`), from ≥ 0.9 to 0.3–0.5. Every other field kept its bucket, so no new false alarm. Photos have no text layer, so nothing changes there.
- Injected set unchanged: 60/60 planted errors to review; 0/3996 and 0/3857 clean ground-truth fields to review.
- Still missed: the 2 party names on `inv_00014` and the messy shipper address (see the diagnosis entry). The frozen test numbers above are calibration only; accuracy is unchanged.

## 2026-11-17: test ledger name failures diagnosed (read from cache after the freeze, 0 API calls)

- Read after the freeze below, at Salik's request. Per-document details for test_unseen_layout were produced in a scratch folder only; committed reports still hide them.
- **The failure is a name/address boundary moved into the address, not a name cut at a comma.** One clean document, `inv_00014`, printed `Graf, Nina-Albers-Straße 345 14153 Kehl, Germany` (buyer = consignee) and `Rogner, Mansstr. 71 43477 Oschatz, Germany` (notify). The model returned names `Graf, Nina` (twice) and `Rogner, Mans`: a one-word company name followed by a street named after a first name reads as "Surname, Firstname". For Graf the address lost `Nina-` (`Albers-Straße 345 …`); for Rogner the address stayed whole, so `Mans` is in both.
- Why it looked like a comma problem: the shortened address scores 0.921 on the fuzzy metric (≥ 0.9, "right"), so ledger addresses looked 100% right and only names failed. Confidence missed it too: `Graf, Nina` is on the page.
- How rare: in all generated ground truth, a one-word name before an address that starts with a first-name street occurs only on `inv_00014` among one-line-party documents. The diagnostic batch had none (`Kitzmann, Schusterstraße …` is a surname street and was read right). One document, three fields: one pattern, small sample.
- The messy ledger miss is different: `inv_00046_messy` consignee `Talal Ltd` read as `Talat Ltd.` (0.889), a character misread on a photo, already sent to review by the photo rule. `inv_00094_messy` shipper address (not critical): a misread (`Juliettedpad`) plus `, Netherlands` appended, so the country leaked into the address.
- The other clean ledger document, `inv_00078`, missed one middle row (`Black tea in bulk packs`, row 7 of 12): not a party problem, and the reason its correct total was flagged.
- No fix made; options are in the report to Salik.

## 2026-11-17: test_unseen_layout v3 FROZEN, pre-inspection

- **Frozen, pre-inspection.** These are the final v3 held-out-layout results, recorded before anyone looked at a test_unseen_layout document's predictions. From here on, the test ledger failures are read for diagnosis (Salik, holdout decision of 2026-11-16), so `ledger` no longer counts as unseen, and no later number on these 31 documents is a held-out result.
- Model `qwen/qwen3.8-27b` (Groq, thinking off), prompt v3, schema 1.0, metrics as of 2026-11-16 (buyer and consignee names critical, strict descriptions, photo review rule).

| Form | Report | Documents | Critical-field accuracy | All critical right | Line F1 | First-try valid | Error recall | Review load |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Clean | `20261116T100758_…` | 24 | 99.1% | 22/24 | 99.7% | 24/24 | 1/3 | 1/1527 |
| Messy | `20261117T104316_…` | 7 | 98.8% | 6/7 | 100% | 6/7 | 1/2 | 85/395 |

- By template, all critical right: clean `ledger` 10/12, `sidebar` 12/12; messy `ledger` 2/3, `sidebar` 4/4.

## 2026-11-17: ledger diagnostic batch, v3 (10 live calls, nothing changed)

- Model `qwen/qwen3.8-27b` (Groq, thinking off), prompt v3. 10 clean `ledger` invoices, seed 1116, split `diagnostic` (outside every split). Report: `reports/20261117T105402_synthetic_clean_diagnostic_qwen_qwen3.8-27b_reasoning-none_v3.md`.
- Critical-field accuracy **100%**, all critical right **10/10**, first-try valid JSON 10/10, retries 0.
- **The comma hypothesis didn't reproduce.** All 30 party names are exact string matches, including the 4 comma names (`diag_00000` shipper and consignee, `diag_00001` shipper, `diag_00009` consignee), each printed as one `name, address, country` line. Addresses: 22/26 exact; in the other 4 the model added a comma where the page has none (`Kapadia Road Jalandhar` -> `Kapadia Road, Jalandhar`), all ≥ 0.9 fuzzy, so scored right.
- Power: the test ledger rate was about 1 document in 12 with a wrong name (clean) and 1 in 3 (messy), so 0/10 here doesn't rule out a rarer cause. It does rule out "a comma in the name reliably breaks it".
- The only errors were on one line of `diag_00007`: description `Hot-rolled钢 coils` (a Chinese token for "steel"; the page prints `Hot-rolled steel coils`) and unit `CTN` for printed `MT`. The description went to `needs_review` (not in the text layer). The unit was missed at confidence ≥ 0.9: `CTN` is printed on another row, and the text-layer check searches the whole page, not the row.
- No fix made (Salik: diagnosis first).

## 2026-11-17: test_unseen_layout messy, v3, final (1 live call)

- Same model and prompt. Report: `reports/20261117T104316_synthetic_messy_test_unseen_layout_qwen_qwen3.8-27b_reasoning-none_v3.md`. The one live call was the first attempt that Groq rejected (400 `json_validate_failed`) again; it's now cached, and the retry came from cache.
- 7/7 scored: critical-field accuracy **98.8%**, all critical right **6/7**, first-try valid 6/7, retry 1/7, line F1 100%. Errors: `consignee.name` (ledger) and `shipper.address` (not critical). By template: ledger 2/3 all right, sidebar 4/4.
- Calibration: error recall 1/2 (the consignee name, by the photo rule); review load 85/395 (21.5%), all from the photo rule.
- v3 test_unseen_layout is now complete in both forms: clean 24/24 (99.1%, 22/24) and messy 7/7 (98.8%, 6/7). These are the results to freeze if the holdout decision applies.

## 2026-11-16: photos send critical fields to review; descriptions must match exactly (re-scored from cache, 0 API calls)

- Salik's decisions: (1) without a text layer nothing can verify a value, so every critical field of such a document goes to `needs_review` ("no text layer to verify"); its confidence score is unchanged, and non-critical fields keep the threshold. No OCR for now (CLAUDE.md, Known issues). (2) Line descriptions are right only if they match after normalising case, spacing and punctuation: any missing, extra or misspelt word is wrong. Addresses (and names) stay fuzzy; line pairing still uses similarity.
- The critical-field list now lives once, in `app/schemas/constants.py`, for both the metrics and the photo rule.
- Report: `reports/20261116T100758_synthetic_clean_test_unseen_layout_qwen_qwen3.8-27b_reasoning-none_v3.md` (the only complete split; same scores, now with calibration and guardrail rows). The partial sets were re-scored into a scratch folder.

| Set | Descriptions right | All fields right | Critical-field accuracy | Error recall | Review load |
| --- | --- | --- | --- | --- | --- |
| Clean, seen (dev 27) | 256/256 -> **252/256** | 99.90% -> 99.71% (2043/2049) | 100% -> 100% | 2/2 -> **6/6** | 4/2047 -> **0/2043** |
| Clean, unseen (24) | 179/179 -> 179/179 | 99.80% -> 99.80% | 99.1% -> 99.1% | 1/3 -> 1/3 | 1/1527 -> 1/1527 |
| Messy, seen (dev 2) | 27/27 -> 27/27 | 99.50% -> 99.50% | 97.4% -> 97.4% | 0/1 -> **1/1** | 0/199 -> **38/199 (19.1%)** |
| Messy, unseen (6 of 7) | 32/32 -> 32/32 | 99.35% -> 99.35% | 98.5% -> 98.5% | 0/2 -> **1/2** | 0/304 -> **67/304 (22.0%)** |
| **All cached v3** | | | | 3/8 -> **9/12 (75%)** | 5/4077 -> 106/4073 (2.6%) |

- Descriptions: the only change is inv_00025's four garbles ("Stainless surgical scissors" for "Stainless steel surgical scissors", "tery", "stee", "part"), now wrong and all already in review, so the old "needless" reviews become caught errors.
- Photos: the incoterm misread (CIP as CIF) and the messy unseen consignee name are now caught. Still missed: a messy shipper address (not critical) and the two clean unseen party names that are on the page as extracted. Review load on photos is about a fifth of correct fields (105/503): every critical field, every time. On clean PDFs it stays 1/3570.
- Injected set unchanged: 60/60 planted errors to review; 0 of 7,853 clean ground-truth fields sent to review (scored as digital PDFs that print exactly their values).
- Sample size: still 12 real errors in all, 3 of them on photos.

## 2026-11-16: confidence scoring and calibration, first measurement (branch `feat/phase4-confidence`, 0 API calls)

- `app/pipeline/confidence.py`: per-field confidence from documented points, no model self-report. Base 0.75 with a text layer, 0.60 without; +0.20 if the value is found in the text layer, −0.45 if not; −0.10 retry, −0.05 repaired keys, −0.10 number format reinterpreted, −0.05 set by a rule; a failed check caps it at 0.10 (`failed_check`). Below 0.50: `needs_review`. Not text-checked: None values and country codes.
- Re-scored cached v3 extractions `--offline` (scores unchanged). "Sent to review" = `needs_review` or `failed_check`.

| Set (source) | Fields | Wrong | Wrong sent to review (error recall) | Correct sent to review (review load) |
| --- | --- | --- | --- | --- |
| Clean, seen, dev 27 (PDF text layer) | 2049 | 2 | 2/2 (both `failed_check`: inv_00025's units) | 4/2047 (0.2%) |
| Clean, unseen 24 (PDF text layer) | 1530 | 3 | 1/3 (`needs_review`) | 1/1527 (0.1%) |
| Messy, seen, dev 2 (image) | 200 | 1 | 0/1 | 0/199 |
| Messy, unseen 6 of 7 (image) | 306 | 2 | 0/2 | 0/304 |
| **All cached v3** | 4085 | 8 | **3/8 (37.5%)** | 5/4077 (0.12%) |

- Calibration on clean PDFs (seen + unseen): confidence ≥ 0.9: 3001/3003 right; 0.7–0.9: 557/557; 0.5–0.7: 11/11; 0.3–0.5: 4/5; below 0.3: 1/3. Messy images all score 0.60: no text layer, so confidence can't separate right from wrong.
- The 4 "needless" dev reviews are real garbles that the ≥ 0.9 fuzzy metric forgives (inv_00025: "Stainless surgical scissors" for "Stainless steel surgical scissors", "tery", "stee", "part"). The 1 unseen review load is a correct value with a failed check (likely the total, made short by the one missed line; the test split shows totals only).
- Missed: the 3 messy errors (no text layer; incoterm CIP read as CIF, a consignee name and a shipper address) and 2 clean unseen party names that scored ≥ 0.9, so they appear on the page as extracted. That fits the ledger hypothesis (a name cut at a comma is still on the page); the diagnostic batch will tell.
- Injected planted errors (guardrail path; JSON, no page): 60/60 planted fields sent to review. Review load on clean ground truth without a page: 0/3996 and 0/3857 fields. With the 8 extraction errors: 63/68 (92.6%), but the two measure different things: planted errors are printed on the document, so only the guardrails can catch them.
- Sample size: 8 real extraction errors. One more caught moves recall by 12.5 points; the 95% interval for 3/8 is roughly 14% to 69%.

## 2026-11-16: guardrails re-measured: rounding-only total tolerance, exact code synonyms (0 API calls)

- Salik's decisions: (1) the total check allows rounding only, half a cent per line amount plus half a cent for the total (1 cent on a one-line invoice), instead of 0.5% of the total; charges and discounts come with prompt v4 (CLAUDE.md, Known issues). (2) Exact synonyms map before validation and the printed value is recorded in `rules_applied`: C&F and CNF -> CFR, RMB -> CNY, US$ -> USD. DDU (not DAP) and Rs (several rupees) stay flagged.
- The injected set was regenerated (seed 2026, 60 invoices): C&F, CNF, RMB and US$ are no longer planted as errors.
- Catch rate: **60/60 = 100%**; every kind 10/10 (wrong totals 5/10 -> 10/10). Every wrong line amount now also flags the total (10/10), as it should: the lines no longer add up.
- False alarms: 0/60 and 0/53 on clean ground truth; cached v3 extractions unchanged from the entry below (0 fully correct documents flagged in any set; scores unchanged; no cached answer used a synonym).
- Caveat: synthetic totals are exactly the line sum. On a real invoice with freight, insurance or a discount, this check flags the total until v4 extracts those charges.

## 2026-11-16: Phase 4 guardrails, first measurement (branch `feat/phase4-guardrails`, 0 API calls)

- `app/pipeline/validate.py` runs after the rules: line math (±0.01), invoice total (±0.5% of the total, DESIGN.md §5.4), net ≤ gross, Incoterm list, ISO 4217 currency, ISO 3166 country per party, date sanity (not after `as_of`, not more than 2 years before). Evals judge dates as of the generator's anchor, 2026-10-01. The HS existence check waits for Phase 5.
- Injected set: 60 invoices (seed 2026, `data/synthetic_injected`, split `injected`), one planted error each, 10 per kind, JSON only. Guardrail report: `python -m evals.guardrails` (written to a scratch folder, not `evals/reports`).

| Error | Caught | How |
| --- | --- | --- |
| wrong line amount | 10/10 | line math (6 also flagged the total) |
| wrong total | **5/10** | invoice total; the 5 misses are typos of 0.36 to 20 on totals of 200k to 1.6M, all under the 0.5% tolerance |
| net > gross | 10/10 | weights |
| invalid Incoterm (C&F, DDU, ...) | 10/10 | rejected by the extraction schema (closed list), so in the pipeline: retry, then `needs_review` |
| future date | 10/10 | date sanity |
| non-ISO currency (RMB, US$, ...) | 10/10 | ISO 4217 |
| **All** | **55/60 = 91.7%** | target ≥ 95% |

- False alarms on clean ground truth: 0/60 (injected set before planting) and 0/53 (synthetic_clean dev).
- False alarms on cached v3 extractions (re-scored `--offline`; scores unchanged):

| Set | Alarms on wrong / correct values | Fully correct documents flagged |
| --- | --- | --- |
| Clean, seen (dev, 27) | 2 / 0 (inv_00025's two garbled units) | 0/26 |
| Clean, unseen (24) | 0 / 1 | 0/22 |
| Messy, seen (dev, 2) | 0 / 0 | 0/1 |
| Messy, unseen (6 of 7) | 0 / 0 | 0/4 |

  - The one alarm on a correct value is on a document that isn't fully correct. The test split shows totals only, but the likely cause is the one missed line: the line amounts then sum short, so the correct total is flagged.
- Harness fixes on the way: `evals.run` read every JSON in the reports folder as an eval report (a guardrail report there crashed it); check notes broke the markdown failure table.
- Open: the total tolerance (0.5% hides small typos on large totals; see CLAUDE.md), confidence scoring and the calibration table (rest of the Phase 4 gate).

## 2026-11-16: buyer and consignee names become critical fields (metric change, re-scored from cache)

- Critical fields are now invoice number, total, currency, Incoterm, **buyer name, consignee name** and line amounts (Salik: a wrong consignee is a serious customs error). `evals/metrics.py`, DESIGN.md §6. Same model, prompt `v3` and cached answers; `--offline`, 0 API calls.
- Report (final): `reports/20261116T074314_synthetic_clean_test_unseen_layout_qwen_qwen3.8-27b_reasoning-none_v3.md`. The other sets are still partial; their re-scores went to a scratch folder.

| Set | n | Critical-field accuracy, old -> new | All critical right, old -> new | Critical fields that failed |
| --- | --- | --- | --- | --- |
| Clean, seen (dev) | 27 of 53 | 100% -> 100% | 27/27 -> 27/27 | none |
| Clean, unseen | 24 of 24 | 99.6% -> 99.1% | 23/24 -> 22/24 | `buyer.name` 23/24, `consignee.name` 23/24 (`ledger`), and one missed line |
| Messy, seen (dev) | 2 of 16 | 97.1% -> 97.4% | 1/2 -> 1/2 | `incoterm` (CIP read as CIF) |
| Messy, unseen | 6 of 7 (offline) | 100% -> 98.5% | 6/6 -> 5/6 | `consignee.name` 5/6 (`ledger`) |

- The unseen name errors are all on `ledger`. In clean, the name errors and the missed line are on different documents. Messy unseen is 6 of 7 because one first attempt (a Groq rejection) isn't cached; it needs one call.
- Next: a diagnostic batch of 10 `ledger` invoices outside every split (`data/diagnostic_ledger`, seed 1116) to find out why party names fail on that layout. Ledger is the only layout that prints a party as one line, `name, address, country`; the 7 others put the name on its own line. About a quarter of generated company names contain commas ("Zarar, Zaafir and Nabeel").

## 2026-11-16: model call time reported apart from waits (harness only, no score change)

- Reports now split each document's time into **model call time** (the final HTTP attempt, request to response), **retry backoff wait** (the SDK's failed attempts and their backoff) and **throttle wait** (client-side, under per-minute limits). The p95 target (< 30 s, DESIGN.md §1) applies to model call time only (Salik, 2026-11-16).
- Not re-derivable from the cache: calls before this change recorded only `latency_ms`, which is model call time plus backoff (throttle wait wasn't recorded at all). Those calls show "not recorded"; model call figures count only documents whose every call has the split and show their n. The old figure stays as "Call + backoff (all documents)".
- Why: dev clean's p95 of 32.6 s came from 2026-11-12 calls that waited on Groq 429 backoff (up to 30 s per retry), not from the model.
- Scores are unchanged: an offline re-score of the 27 cached dev clean documents gives the same numbers.

## 2026-11-16: v3 on test_unseen_layout (reporting only)

- Model `qwen/qwen3.8-27b` on Groq, thinking off, JSON object mode, prompt `v3` with the deterministic fixes, schema `1.0`, git `0412371`. Reporting split: no prompt or code was tuned on it, and the report holds no per-document details.
- Report (clean): `reports/20261116T065309_synthetic_clean_test_unseen_layout_qwen_qwen3.8-27b_reasoning-none_v3.md`, re-scored from cache; identical to the live run. 13 documents were answered on 2026-11-12, 11 today.
- Clean, unseen (n=24, `ledger` 12, `sidebar` 12): critical-field accuracy 99.6%, all critical right 95.8% (23/24), line precision 100%, recall 99.4%, F1 99.7%, first-try valid JSON 24/24, retries 0, `needs_review` 0. Latency p50 1.7 s, p95 2.4 s. Cost $0.
  - Only `ledger` lost points (critical 99.2%, all critical right 11/12). One gold line of 180 was missed, which counts against line amounts. `buyer.name`, `consignee.name` and `notify_party.name` are 23/24 each; reference buyers 5/5.
  - Seen clean dev (n=16) was 100% critical; the unseen gap is under 1 point on this sample.
- Messy, unseen (n=7, `ledger` 3, `sidebar` 4), live run: critical-field accuracy 100%, all critical right 7/7, line F1 100%, first-try valid 6/7 (one first answer rejected by Groq with `json_validate_failed`; the retry was valid), `needs_review` 0, 7 numbers normalised, 0 ambiguous. Latency p50 1.7 s, p95 9.5 s. Cost per document "unknown": a rejected call reports no usage.
  - **No final messy report yet.** Re-scoring offline gave 6/7, because the cache didn't store Groq's rejection, so the rejected document's first attempt couldn't be replayed. Fixed the same day: the cache now stores schema rejections (regression tests in `tests/test_llm.py` and `tests/test_extract.py`). The first attempt of that one document must be called once more before the final report can be written from cache.
- Harness: interim runs went to a scratch folder (`REPORTS_DIR`), not `evals/reports`.

## 2026-11-12: v3 + deterministic fixes, re-scored from cache (0 API calls)

- Same model, prompt `v3` and 18 documents as the previous entry; git `7599309`; `evals.run --offline`, so every answer came from the cache.
- Reports: `reports/20261112T123816_synthetic_clean_dev_qwen_qwen3.8-27b_reasoning-none_v3.md`, `reports/20261112T123831_synthetic_messy_dev_qwen_qwen3.8-27b_reasoning-none_v3.md`.
- Reference-buyer rule: inv_00002's buyer "As consignee" now resolves to the consignee. Reference buyers 5/6 -> 6/6; all fields (clean) 1085/1090 -> 1088/1090.
- Unit list: `line.unit` accuracy stays 129/131 (nothing is guessed), but both wrong units (inv_00025 `MET`, `SE`) are now `failed_check`, with no false flags on the 129 right ones.
- Number normalisation: inv_00003_messy's first answer (`"207,732.88"` style amounts) is now valid. Messy first-try valid 1/2 -> 2/2, retries 1 -> 0, 13 numbers normalised, 0 ambiguous.
- Unchanged: critical-field accuracy clean 100%, messy 97.1%; the one critical error left is inv_00003_messy's incoterm (CIP read as CIF on a blurred photo).

## 2026-11-12: v3 on the v2 baseline's 18 documents

- Model `qwen/qwen3.8-27b` on Groq, thinking off, JSON object mode, prompt `v3` (exact key list in the system prompt and the retry), key repair on, schema `1.0`, git `61b0030`.
- Reports: `reports/20261112T121955_synthetic_clean_dev_qwen_qwen3.8-27b_reasoning-none_v3.md`, `reports/20261112T122001_synthetic_messy_dev_qwen_qwen3.8-27b_reasoning-none_v3.md`. Same documents as the v2 baseline: the first 16 clean dev and the first 2 messy dev by id, all seen layouts.
- Clean (n=16), v2 -> v3: first-try valid JSON 8/16 -> 16/16, retries 8 -> 0, `needs_review` 2 -> 0, critical-field accuracy 85.1% -> 100%, all critical right 14/16 -> 16/16, all fields 96.1% -> 99.5%, tokens per document 5,686 -> 3,859 (-32%), latency p50 2.2 s -> 1.8 s.
- Messy (n=2), v2 -> v3: first-try valid 2/2 -> 1/2 (inv_00003_messy wrote amounts that weren't plain decimals; the retry fixed it), all critical right 1/2 both (incoterm CIP read as CIF in both versions: a reading error on a blurred photo).
- Keys repaired 0 and ignored 0 in v3: the key list in the prompt alone fixed key names. The repair code is a safety net; on the cached v2 answers it raises first-try validity from 10/18 to 14/18.
- New errors in v3 (both clean, both non-critical): inv_00002 (a "Same as consignee" buyer) has the buyer name "As consignee" with no address or country (v2 was right; reference buyers 6/6 -> 5/6); inv_00025 units "MET" for MT and "SE" for SET, the same garbling seen in v2 key names but in values, which key repair can't catch.
- Tokens per call: first-attempt input 3,211 -> 3,375 and output 529 -> 484 (clean). JSON object mode did not cut input by ~1,000 as expected; the saving per document comes from no retries and shorter output.
- Small sample (18 documents, one model, seen layouts only); one document moves a clean rate by 6 points.

## 2026-11-11: v2 baseline on Groq (partial by decision)

- Model `qwen/qwen3.8-27b` on Groq (free tier), thinking off (`reasoning_effort=none`), provider strict mode off, prompt `v2` (compact line items), schema `1.0`, git `ff12136`.
- Reports: `reports/20261111T094649_synthetic_clean_dev_qwen_qwen3.8-27b_reasoning-none_v2.md`, `reports/20261111T100956_synthetic_messy_dev_qwen_qwen3.8-27b_reasoning-none_v2.md`.
- **This is the v2 baseline, on 18 documents:** 16 clean dev (seen layouts) and 2 messy dev (seen). No unseen documents. Groq's 200,000 tokens a day stopped the run, and Salik chose not to spend about 2.5 days finishing it, because the failure mode was already clear.
- Clean dev, seen (n=16): critical-field accuracy 85.1%, all critical right 87.5% (14/16), line precision 100%, recall 84.0%, F1 91.3%, first-try valid JSON 50.0% (8/16), retry rate 50.0% (8/16), `needs_review` 12.5% (2/16). Latency p50 2.2 s, p95 5.1 s. Cost $0.
- Messy dev, seen (n=2): critical-field accuracy 97.1%, all critical right 50.0% (1/2), first-try valid JSON 100% (2/2), retry rate 0%.
- Every document with valid output was fully correct. Both clean failures are schema failures: invented keys survived the retry (`document_type` on inv_00025, where the retry repeated the first answer exactly; `country_of_origin` on inv_00003). First-try errors were misspelt keys (`shiftper`, `shift`, `" shipper"`, `buyer.	assertEquals`) and invented keys (`document_type`, `notes`, `company_name`, `country_of_origin`).
- What changed and why: model switched from Gemini (20 requests a day) to Groq to stay on the free tier; prompt v2 writes line items as arrays to fit Groq's 1,000 output tokens a minute.
- Next: Phase 3 starts early with schema reliability (prompt v3 plus key repair in code).

## 2026-11-11: baseline v1, partial (quota)

- Model `gemini-3.5-flash` (Google AI Studio free tier), prompt `v1`, schema `1.0`.
- Reports: `reports/20261111T082937_synthetic_clean_dev_gemini-3.5-flash_v1.md`, `reports/20261111T083025_synthetic_messy_dev_gemini-3.5-flash_v1.md`.
- **Incomplete:** clean dev 10/53 scored, messy dev 2/16. The free tier allows 20 requests per model per day (`GenerateRequestsPerDayPerProjectPerModel-FreeTier`), and the quota ran out. Rerunning the same commands scores the rest; the scored documents come from cache.
- On what was scored: critical-field accuracy 100% (clean n=10, messy n=2), every field 100%, line F1 100%, no retries or `needs_review`. Reference buyers ("Same as consignee"): 4/4 right. Latency p50 15.1 s, p95 25.7 s (clean). Cost $0 (free tier).
- Too few documents to read anything into yet; the full 53 + 16 decide the baseline.
- What changed: first run. The dataset now prints "Same as consignee" (or a synonym) in the buyer block for about 30% of buyers who are the consignee, and reports score that as its own slice.
- Model choice: `gemini-3.7-flash` and `gemini-3.8-flash` hung until timeout even on a two-word prompt, and `gemini-3.6-flash` returned 503, so `gemini-3.5-flash` is the primary.
