"""A client that never calls a provider, for re-scoring from the cache at zero cost."""

from app.llm.client import LLMRequest, LLMResponse, NotCachedError


class OfflineClient:
    """Placed under the cache: any request the cache can't answer fails instead."""

    def __init__(self, model: str) -> None:
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def complete(self, request: LLMRequest) -> LLMResponse:
        raise NotCachedError(
            f"{self._model}: attempt {request.attempt} isn't cached (offline run, no API call)"
        )
