# HS eval 20261123T132503_hs_gold_test_openai_gpt-oss-120b_reasoning-low_query-v1_rerank-v1

- Git: `e5495aeab0 (uncommitted changes)`, run at 2026-11-23 13:25 UTC
- Model `openai/gpt-oss-120b` (reasoning low), prompts `hs_query/v1` and `hs_rerank/v1`; retrieval `BAAI/bge-small-en-v1.5` vector + full-text over PCT 2026-27, description and rewrite fused, top 10
- hs_gold `test`: 23/23 items scored; gold codes verified by a person: 0/23
- LLM calls: 0 made, 47 from cache

## Accuracy by level (every item counts; needs_review and failed are misses)

| Measure | Heading (4) | Subheading (6) | National (8) |
| --- | --- | --- | --- |
| Retrieval recall@10 | 82.6% (19/23) | 82.6% (19/23) | 78.3% (18/23) |
| Retrieval order top-1 | 56.5% (13/23) | 43.5% (10/23) | 39.1% (9/23) |
| Retrieval order top-3 | 78.3% (18/23) | 73.9% (17/23) | 60.9% (14/23) |
| **Rerank top-1** | 82.6% (19/23) | 78.3% (18/23) | 69.6% (16/23) |
| **Rerank top-3** | 82.6% (19/23) | 78.3% (18/23) | 73.9% (17/23) |

## Operations

- Stage outcomes: ok 23
- Query rewrite ok: 100.0% (23/23)
- Rerank first-try valid answer: 95.7% (22/23); retries 1
- Tokens: 31076 in, 7910 out
- Model call time p50 0.9 s, p95 1.7 s

## Calibration: national top-1 by retrieval similarity of the first suggestion

| Similarity | Items | National top-1 |
| --- | --- | --- |
| 0.00 to 0.60 | 0 | — |
| 0.60 to 0.70 | 11 | 72.7% (8/11) |
| 0.70 to 0.80 | 9 | 66.7% (6/9) |
| 0.80 to 1.00 | 2 | 100.0% (2/2) |
