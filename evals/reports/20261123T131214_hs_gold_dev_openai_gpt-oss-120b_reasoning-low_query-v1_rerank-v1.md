# HS eval 20261123T131214_hs_gold_dev_openai_gpt-oss-120b_reasoning-low_query-v1_rerank-v1

- Git: `e5495aeab0 (uncommitted changes)`, run at 2026-11-23 13:12 UTC
- Model `openai/gpt-oss-120b` (reasoning low), prompts `hs_query/v1` and `hs_rerank/v1`; retrieval `BAAI/bge-small-en-v1.5` vector + full-text over PCT 2026-27, description and rewrite fused, top 10
- hs_gold `dev`: 63/63 items scored; gold codes verified by a person: 0/63
- LLM calls: 0 made, 126 from cache

## Accuracy by level (every item counts; needs_review and failed are misses)

| Measure | Heading (4) | Subheading (6) | National (8) |
| --- | --- | --- | --- |
| Retrieval recall@10 | 92.1% (58/63) | 81.0% (51/63) | 77.8% (49/63) |
| Retrieval order top-1 | 66.7% (42/63) | 47.6% (30/63) | 42.9% (27/63) |
| Retrieval order top-3 | 76.2% (48/63) | 61.9% (39/63) | 57.1% (36/63) |
| **Rerank top-1** | 88.9% (56/63) | 76.2% (48/63) | 74.6% (47/63) |
| **Rerank top-3** | 90.5% (57/63) | 81.0% (51/63) | 77.8% (49/63) |

## Operations

- Stage outcomes: ok 63
- Query rewrite ok: 100.0% (63/63)
- Rerank first-try valid answer: 100.0% (63/63); retries 0
- Tokens: 81811 in, 19179 out
- Model call time p50 1.0 s, p95 2.2 s

## Calibration: national top-1 by retrieval similarity of the first suggestion

| Similarity | Items | National top-1 |
| --- | --- | --- |
| 0.00 to 0.60 | 0 | — |
| 0.60 to 0.70 | 28 | 67.9% (19/28) |
| 0.70 to 0.80 | 31 | 80.6% (25/31) |
| 0.80 to 1.00 | 4 | 75.0% (3/4) |

## Misses at national level (dev only)

- **hs_001** '100% COTTON CARDED YARN NE 20/1 FOR WEAVING, ON CONES': gold `5205.1200`, ok, suggested ['5205.3500']; retrieved top 5: 5203.0000, 5108.1000, 5205.4100, 5201.0090, 5206.4100 (gold not retrieved)
- **hs_010** 'COTTON GREIGE FABRIC 40X40 133X72 63 INCH 110 GSM': gold `5208.1200`, ok, suggested ['5208.1900']; retrieved top 5: 5208.1900, 5208.2900, 5208.3900, 5209.1900, 5209.2900 (gold not retrieved)
- **hs_030** 'INSTANT NOODLES CHICKEN FLAVOUR 70 G X 40 PCS/CTN': gold `1902.3000`, ok, suggested ['2106.9030', '1101.0010']; retrieved top 5: 1101.0010, 1102.9010, 1101.0020, 2106.9030, 0902.1000 (gold not retrieved)
- **hs_031** 'PP HOMOPOLYMER INJECTION GRADE MFI 12, 25 KG BAGS': gold `3902.1000`, ok, suggested ['5501.4000', '5503.4000', '5506.4000']; retrieved top 5: 5501.4000, 5503.4000, 5506.4000, 3915.3000, 4202.1210 (gold not retrieved)
- **hs_032** 'PET RESIN BOTTLE GRADE IV 0.80, 1100 KG JUMBO BAGS': gold `3907.6120`, ok, suggested ['3907.6920', '3907.6120']; retrieved top 5: 3907.6920, 3907.6120, 3907.6110, 3907.5000, 3909.4000
- **hs_034** 'PVC SUSPENSION RESIN K67, 25 KG BAGS': gold `3904.1090`, ok, suggested ['3904.4000']; retrieved top 5: 3909.4000, 3909.2000, 3909.5000, 3904.5000, 3909.3900 (gold not retrieved)
- **hs_038** 'PARACETAMOL BP POWDER (API), 25 KG FIBRE DRUMS': gold `2924.2910`, ok, suggested ['3004.9092']; retrieved top 5: 3601.0000, 3004.9092, 7603.1000, 3004.9070, 3004.9091 (gold not retrieved)
- **hs_039** 'AMOXICILLIN TRIHYDRATE COMPACTED BP/USP (API)': gold `2941.1000`, ok, suggested ['2941.9090']; retrieved top 5: 2941.9090, 3004.2000, 3004.6000, 3003.2000, 2941.9060 (gold not retrieved)
- **hs_042** 'LDPE CARRIER BAGS 40X50 CM, PRINTED LOGO': gold `3923.2100`, ok, suggested ['6305.3300', '4202.1290']; retrieved top 5: 4202.1210, 4819.3000, 4202.1220, 4202.1290, 8443.1200 (gold not retrieved)
- **hs_043** 'EMPTY PET BOTTLES 1.5 LTR': gold `3923.3010`, ok, suggested ['3907.6920', '3923.3010']; retrieved top 5: 3907.6920, 3923.3010, 7613.0010, 8422.3000, 3923.9010
- **hs_044** 'BOPP PACKING TAPE 48MM X 100M, CLEAR': gold `3919.1090`, ok, suggested ['3919.9010', '3919.1030']; retrieved top 5: 3919.9010, 3919.1010, 3005.1010, 3919.9020, 3919.1030 (gold not retrieved)
- **hs_045** 'HR COIL SS400 2.0MM X 1250MM, PRIME': gold `7208.3990`, ok, suggested ['7219.1400']; retrieved top 5: 7219.1200, 7219.1300, 7219.2310, 7219.2200, 7219.1400 (gold not retrieved)
- **hs_049** 'ALUMINIUM FOIL JUMBO ROLL 12 MICRON X 1000 MM, PLAIN': gold `7607.1100`, ok, suggested ['7607.1920']; retrieved top 5: 7607.1920, 7606.9210, 7219.2200, 7606.9190, 7606.9290 (gold not retrieved)
- **hs_050** 'ENAMELLED COPPER WIRE 1.00 MM, CLASS 155, ON SPOOLS': gold `8544.1190`, ok, suggested ['8544.1110', '7408.1900', '7408.2900']; retrieved top 5: 7408.2900, 7408.1900, 7408.2100, 7408.2200, 7403.1200 (gold not retrieved)
- **hs_080** 'SHEESHAM WOOD DINING TABLE 6 SEATER': gold `9403.6000`, ok, suggested ['9403.4000']; retrieved top 5: 4419.2000, 9403.5020, 9403.5010, 4405.0000, 9401.4100 (gold not retrieved)
- **hs_081** 'PLASTIC CHAIR MONOBLOC, STACKABLE': gold `9401.8000`, ok, suggested ['9403.7000']; retrieved top 5: 3918.9000, 3918.1000, 9402.1010, 3916.9000, 9403.7000 (gold not retrieved)
