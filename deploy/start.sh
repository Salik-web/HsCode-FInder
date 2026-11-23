#!/usr/bin/env bash
# Container entry. Every step is idempotent, because a free Space sleeps and restarts
# with an empty disk: migrations no-op when applied, the tariff is embedded only on a new
# database, and the samples are re-processed from the committed cache when their page
# images are gone. Uploads whose files vanished are purged by the worker's cleanup.
set -euo pipefail
cd /app

alembic upgrade head
python -m tariff.ingest --csv tariff/pct_2026-27.csv --version 2026-27 --if-empty
python -m app.demo seed || echo "WARNING: demo samples not seeded; uploads still work" >&2

# The API listens on localhost only: visitors reach the UI, and the UI enforces the
# per-session cap before the API enforces the daily ones.
uvicorn app.api.main:create_app --factory --host 127.0.0.1 --port 8000 &
python - <<'PY'
import time, urllib.request
for _ in range(60):
    try:
        urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2)
        break
    except OSError:
        time.sleep(1)
PY
python -m app.worker &
# XSRF and CORS checks break file uploads inside the Space's iframe; the UI is the only
# public port and the API isn't reachable from outside.
streamlit run ui/app.py \
    --server.port 7860 --server.address 0.0.0.0 --server.headless true \
    --server.maxUploadSize 10 --server.enableXsrfProtection false --server.enableCORS false \
    --browser.gatherUsageStats false &

# If any process dies, exit so the Space restarts the container.
wait -n
exit 1
