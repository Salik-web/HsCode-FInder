# syntax=docker/dockerfile:1
# One image for the public demo: FastAPI + worker + Streamlit review UI.
# Built by .github/workflows/deploy.yml and run by the Hugging Face Space.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    HF_HOME=/opt/models

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /app

# Dependencies first, so a code change doesn't reinstall them. Runtime groups only: no
# dev, evals or synth (no Playwright or Chromium); torch is the CPU build (pyproject).
# uv's download cache lives in a build cache mount, so it never lands in the image.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv     uv sync --frozen --no-default-groups --group ui --no-install-project

# The embedding model ships inside the image; at runtime the Hub is never contacted.
RUN /opt/venv/bin/python -c \
    "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-en-v1.5')"

COPY alembic.ini ./
COPY app ./app
COPY ui ./ui
COPY prompts ./prompts
COPY tariff ./tariff
COPY demo ./demo
COPY deploy/start.sh ./deploy/start.sh

# Hugging Face Spaces run containers as uid 1000; only storage/ is written to.
RUN useradd --uid 1000 --create-home app && mkdir -p /app/storage && chown app /app/storage
USER app

ENV PATH=/opt/venv/bin:$PATH \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    DEMO_MODE=true \
    LLM_CACHE_ENABLED=false \
    STORAGE_DIR=/app/storage

EXPOSE 7860
CMD ["bash", "deploy/start.sh"]
