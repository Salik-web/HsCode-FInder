"""Assemble the client stack from settings: cache -> tracing -> provider, per model."""

import logging

from langfuse import Langfuse

from app.config import ProviderConfig, Settings
from app.llm.cache import CachingClient
from app.llm.client import LLMClient
from app.llm.fallback import FallbackClient
from app.llm.openai_compatible import OpenAICompatibleClient
from app.llm.tracing import Tracer, TracingClient

logger = logging.getLogger(__name__)


def build_client(settings: Settings, use_fallback: bool) -> LLMClient:
    """Build the configured client. Switching provider or model is configuration only.

    The cache sits outside tracing, so a cache hit isn't traced as an LLM call, and each
    model has its own cache, so a fallback answer is never stored as the primary's.
    """
    tracer = _build_tracer(settings)
    primary = _provider_stack(settings.primary_provider(), settings, tracer)
    fallback_config = settings.fallback_provider_config() if use_fallback else None
    if fallback_config is None:
        return primary
    return FallbackClient(primary, _provider_stack(fallback_config, settings, tracer))


def _provider_stack(config: ProviderConfig, settings: Settings, tracer: Tracer | None) -> LLMClient:
    client: LLMClient = OpenAICompatibleClient(
        config,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        max_tokens=settings.llm_max_output_tokens,
    )
    if tracer is not None:
        client = TracingClient(client, tracer)
    return CachingClient(client, settings.cache_dir)


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
