"""Disk cache for LLM responses, so re-scoring and resuming a run cost nothing."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel

from app.llm.client import LLMClient, LLMRequest, LLMResponse


class CacheEntry(BaseModel):
    document_sha256: str
    model: str
    prompt_version: str
    attempt: int
    response: LLMResponse


def cache_key(request: LLMRequest, model: str) -> str:
    """sha256 of file, model, prompt version and attempt, plus the full request.

    The request digest means a preprocessing change (resolution, text layer on or off) can
    never be answered from a response to different input.
    """
    request_digest = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
    parts = [request.document_sha256, model, request.prompt_version, request.attempt]
    return hashlib.sha256(json.dumps([*parts, request_digest]).encode()).hexdigest()


class CachingClient:
    """Wraps a client; only successful responses are stored, so failures are retried later."""

    def __init__(self, inner: LLMClient, cache_dir: Path) -> None:
        self._inner = inner
        self._cache_dir = cache_dir

    @property
    def model(self) -> str:
        return self._inner.model

    def complete(self, request: LLMRequest) -> LLMResponse:
        path = self._path(cache_key(request, self.model))
        if path.exists():
            entry = CacheEntry.model_validate_json(path.read_bytes())
            return entry.response.model_copy(update={"cached": True})
        response = self._inner.complete(request)
        entry = CacheEntry(
            document_sha256=request.document_sha256,
            model=self.model,
            prompt_version=request.prompt_version,
            attempt=request.attempt,
            response=response,
        )
        _write_atomically(path, entry.model_dump_json(indent=2).encode())
        return response

    def _path(self, key: str) -> Path:
        return self._cache_dir / key[:2] / f"{key}.json"


def _write_atomically(path: Path, data: bytes) -> None:
    """Concurrent workers and interrupted runs must never leave a half-written entry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
