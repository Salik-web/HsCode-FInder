"""Switch to a second model when the first is rate-limited (design rule 10)."""

import logging

from app.llm.client import LLMClient, LLMRequest, LLMResponse, RateLimitedError

logger = logging.getLogger(__name__)


class FallbackClient:
    """Tries `primary`, then `fallback` on a rate limit only.

    Other errors propagate: a bad request would fail the same way on the second model, and
    silently answering with another model would hide a real fault. Each response records
    which model actually answered.
    """

    def __init__(self, primary: LLMClient, fallback: LLMClient) -> None:
        self._primary = primary
        self._fallback = fallback

    @property
    def model(self) -> str:
        return f"{self._primary.model} (fallback {self._fallback.model})"

    def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            return self._primary.complete(request)
        except RateLimitedError:
            logger.warning(
                "primary rate-limited, using fallback",
                extra={"document_sha256": request.document_sha256, "model": self._fallback.model},
            )
            return self._fallback.complete(request)
