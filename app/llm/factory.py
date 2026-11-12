"""Assemble the client stack from settings: cache -> throttle -> tracing -> provider."""

import logging

from langfuse import Langfuse

from app.config import ProviderConfig, Settings
from app.llm.cache import CachingClient
from app.llm.client import LLMClient
from app.llm.fallback import FallbackClient
from app.llm.offline import OfflineClient
from app.llm.openai_compatible import OpenAICompatibleClient
from app.llm.throttle import ThrottledClient
from app.llm.tracing import Tracer, TracingClient

logger = logging.getLogger(__name__)


def build_client(settings: Settings, use_fallback: bool, offline: bool = False) -> LLMClient:
    """Build the configured client. Switching provider or model is configuration only.

    The cache sits outside tracing, so a cache hit isn't traced as an LLM call, and each
    model has its own cache, so a fallback answer is never stored as the primary's.
    `offline` answers from the cache only and never calls a provider.
    """
    tracer = None if offline else _build_tracer(settings)
    primary = _provider_stack(settings.primary_provider(), settings, tracer, offline)
    fallback_config = settings.fallback_provider_config() if use_fallback else None
    if fallback_config is None:
        return primary
    return FallbackClient(primary, _provider_stack(fallback_config, settings, tracer, offline))


def _provider_stack(
    config: ProviderConfig, settings: Settings, tracer: Tracer | None, offline: bool
) -> LLMClient:
    provider = OpenAICompatibleClient(
        config,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        max_tokens=settings.llm_max_output_tokens,
    )
    if offline:
        # The provider object still supplies the cache-key options; it's never called.
        return CachingClient(
            OfflineClient(provider.model), settings.cache_dir, provider.call_options
        )
    client: LLMClient = provider
    if tracer is not None:
        client = TracingClient(client, tracer)
    # Inside the cache, so answers served from disk never wait for the budget.
    client = ThrottledClient(
        client,
        tokens_per_minute=config.tokens_per_minute,
        requests_per_minute=config.requests_per_minute,
        output_tokens_per_minute=config.output_tokens_per_minute,
    )
    return CachingClient(client, settings.cache_dir, provider.call_options)


def _build_tracer(settings: Settings) -> Tracer | None:
    if settings.langfuse_public_key is None or settings.langfuse_secret_key is None:
        logger.warning("Langfuse keys not set: LLM calls will not be traced")
        return None
    # Langfuse flushes queued traces itself at interpreter exit.
    return Langfuse(
        public_key=settings.langfuse_public_key.get_secret_value(),
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        host=settings.langfuse_host,
    )
