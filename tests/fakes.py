"""Test doubles shared by several test modules."""

from app.llm.client import LLMRequest, LLMResponse


class FakeClient:
    """Returns queued responses or raises queued errors, and records each request."""

    def __init__(self, model: str, results: list[LLMResponse | Exception]) -> None:
        self._model = model
        self._results = list(results)
        self.requests: list[LLMRequest] = []

    @property
    def model(self) -> str:
        return self._model

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result
