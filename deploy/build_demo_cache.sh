#!/usr/bin/env bash
# Fill demo/llm_cache for the sample invoices, from inside the demo image.
#
# Cache keys hash the rendered page images, and PDFium renders differently on Linux than
# on Windows or macOS, so the cache the Space replays must be built by the image itself.
# HS requests carry no images, so their answers are shared across machines.
#
# Usage: deploy/build_demo_cache.sh <database url reachable from the container>
# Needs GROQ_API_KEY in the environment; costs one extraction call per sample, plus HS
# calls for any line not already cached.
set -euo pipefail
database_url="${1:?usage: deploy/build_demo_cache.sh <database url>}"
: "${GROQ_API_KEY:?set GROQ_API_KEY}"
docker build -t tradedoc-ai:local .
MSYS_NO_PATHCONV=1 docker run --rm \
    -e DATABASE_URL="$database_url" -e GROQ_API_KEY \
    -v "$(pwd)/demo/llm_cache:/app/demo/llm_cache" \
    --entrypoint bash tradedoc-ai:local -c \
    "alembic upgrade head \
     && python -m tariff.ingest --csv tariff/pct_2026-27.csv --version 2026-27 --if-empty \
     && python -m app.demo seed --live"
