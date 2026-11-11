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
    # Entries written before call options existed (the Gemini baseline) have none.
    call_options: dict[str, str] = {}
    response: LLMResponse


def cache_key(request: LLMRequest, model: str, call_options: dict[str, str]) -> str:
    """sha256 of file, model, prompt version and attempt, plus the full request.

    The request digest means a preprocessing change (resolution, text layer on or off) can
    never be answered from a response to different input. Call options (such as reasoning
    on or off) join the key only when set, so keys made before they existed stay valid.
    """
    request_digest = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
    parts: list[object] = [request.document_sha256, model, request.prompt_version]
    parts += [request.attempt, request_digest]
    if call_options:
        parts.append(sorted(call_options.items()))
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


class CachingClient:
    """Wraps a client; only successful responses are stored, so failures are retried later."""

    def __init__(self, inner: LLMClient, cache_dir: Path, call_options: dict[str, str]) -> None:
        self._inner = inner
        self._cache_dir = cache_dir
        self._call_options = call_options

    @property
    def model(self) -> str:
        return self._inner.model

    def complete(self, request: LLMRequest) -> LLMResponse:
        path = self._path(cache_key(request, self.model, self._call_options))
        if path.exists():
            entry = CacheEntry.model_validate_json(path.read_bytes())
            return entry.response.model_copy(update={"cached": True})
        response = self._inner.complete(request)
        entry = CacheEntry(
            document_sha256=request.document_sha256,
            model=self.model,
            prompt_version=request.prompt_version,
            attempt=request.attempt,
            call_options=self._call_options,
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
