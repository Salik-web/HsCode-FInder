---
title: TradeDoc AI
emoji: 📄
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
short_description: Invoice extraction with guardrails and HS code suggestions
---

# TradeDoc AI: demo

Reads commercial invoices with a vision LLM, checks every field with deterministic
guardrails, and suggests Pakistan Customs Tariff codes. A person reviews and approves.

- **Upload synthetic documents only**, never a real invoice. Uploads are deleted within an hour.
- Uploads are limited per session and per day; the sample invoices are free to explore.
- **The first load after a quiet spell may take a minute** while the Space wakes up.

Code, eval results and limitations: https://github.com/Salik-web/HsCode-FInder
