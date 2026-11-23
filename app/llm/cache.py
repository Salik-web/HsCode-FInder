"""Disk cache for LLM responses, so re-scoring and resuming a run cost nothing."""

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, model_validator

from app.files import write_atomically
from app.llm.client import LLMClient, LLMRequest, LLMResponse, SchemaViolationError


class CachedRejection(BaseModel):
    """The provider's schema rejection: an answer from the model, so it is replayed too."""

    message: str
    model: str
    latency_ms: int
    # Recorded from 2026-11-16 on, like the same fields on LLMResponse.
    call_ms: int | None = None
    throttle_ms: int | None = None


class CacheEntry(BaseModel):
    document_sha256: str
    model: str
    prompt_version: str
    attempt: int
    # Entries written before call options existed (the Gemini baseline) have none.
    call_options: dict[str, str] = {}
    response: LLMResponse | None = None
    rejection: CachedRejection | None = None

    @model_validator(mode="after")
    def _exactly_one_outcome(self) -> "CacheEntry":
        if (self.response is None) == (self.rejection is None):
            raise ValueError("a cache entry holds either a response or a rejection")
        return self


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
    """Wraps a client; stores answers, so re-scoring replays exactly what the model said.

    An answer is a response or the provider's schema rejection of one (both come from the
    model at temperature 0). Transient failures (rate limits, timeouts) aren't stored, so
    they are retried later.
    """

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
            return _replay(CacheEntry.model_validate_json(path.read_bytes()))
        try:
            response = self._inner.complete(request)
        except SchemaViolationError as error:
            rejection = CachedRejection(
                message=str(error),
                model=error.model,
                latency_ms=error.latency_ms,
                call_ms=error.call_ms,
                throttle_ms=error.throttle_ms,
            )
            self._store(path, request, rejection=rejection)
            raise
        self._store(path, request, response=response)
        return response

    def _store(
        self,
        path: Path,
        request: LLMRequest,
        response: LLMResponse | None = None,
        rejection: CachedRejection | None = None,
    ) -> None:
        entry = CacheEntry(
            document_sha256=request.document_sha256,
            model=self.model,
            prompt_version=request.prompt_version,
            attempt=request.attempt,
            call_options=self._call_options,
            response=response,
            rejection=rejection,
        )
        write_atomically(path, entry.model_dump_json(indent=2).encode())

    def _path(self, key: str) -> Path:
        return self._cache_dir / key[:2] / f"{key}.json"


def _replay(entry: CacheEntry) -> LLMResponse:
    if entry.rejection is not None:
        rejection = entry.rejection
        error = SchemaViolationError(
            rejection.message,
            rejection.model,
            rejection.latency_ms,
            call_ms=rejection.call_ms,
            cached=True,
        )
        error.throttle_ms = rejection.throttle_ms
        raise error
    # The validator guarantees a response when there is no rejection.
    assert entry.response is not None
    return entry.response.model_copy(update={"cached": True})
