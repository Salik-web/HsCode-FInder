"""Application settings, read from the environment and `.env`."""

from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["gemini", "openrouter", "groq"]
Role = Literal["llm", "fallback"]  # the settings prefix for each model's options

# Every provider speaks the OpenAI chat API, so adding one is a base URL and a key setting.
PROVIDER_BASE_URLS: dict[ProviderName, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
}


class MissingApiKeyError(RuntimeError):
    """The configured provider has no API key in the environment."""


class ProviderConfig(BaseModel):
    """Everything needed to call one model; cost is computed from the prices given."""

    name: ProviderName
    base_url: str
    api_key: SecretStr
    model: str
    # Free tiers cost nothing; set real prices to see real cost per document.
    input_usd_per_million_tokens: Decimal
    output_usd_per_million_tokens: Decimal
    # Sent as `reasoning_effort` when set (e.g. "none" turns Qwen 3 thinking off on Groq).
    reasoning_effort: str | None
    # Ask the provider to enforce the JSON schema. Groq's Qwen ignores the schema without it.
    strict_schema: bool
    # Client-side throttling to stay under the provider's per-minute limits. None: no limit.
    tokens_per_minute: int | None
    requests_per_minute: int | None


class Settings(BaseSettings):
    # extra="ignore" because .env also holds POSTGRES_* values for docker-compose.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    cache_dir: Path = Path(".cache/llm")
    reports_dir: Path = Path("evals/reports")

    gemini_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None

    llm_provider: ProviderName = "groq"
    # The only Groq model that accepts images (checked 2026-11-11); qwen3.6-27b isn't served.
    llm_model: str = "qwen/qwen3.8-27b"
    llm_reasoning_effort: str | None = "none"
    llm_strict_schema: bool = True
    # Groq free tier for this model: 8,000 tokens a minute, 1,000 requests a day.
    llm_tokens_per_minute: int | None = 8_000
    llm_requests_per_minute: int | None = 30
    llm_input_usd_per_million_tokens: Decimal = Decimal(0)
    llm_output_usd_per_million_tokens: Decimal = Decimal(0)

    fallback_provider: ProviderName | None = "groq"
    fallback_model: str = "qwen/qwen3.8-27b"
    fallback_reasoning_effort: str | None = "none"
    fallback_strict_schema: bool = True
    fallback_tokens_per_minute: int | None = 8_000
    fallback_requests_per_minute: int | None = 30
    fallback_input_usd_per_million_tokens: Decimal = Decimal(0)
    fallback_output_usd_per_million_tokens: Decimal = Decimal(0)

    llm_timeout_seconds: float = 120.0
    # The OpenAI client retries 5xx, timeouts and (as a last resort) 429 with backoff.
    llm_max_retries: int = 3
    llm_max_output_tokens: int = 16_000
    # Free tiers allow a few requests a minute, so only a little concurrency pays off.
    llm_concurrency: int = 2

    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    @field_validator(
        "llm_reasoning_effort",
        "fallback_reasoning_effort",
        "llm_tokens_per_minute",
        "llm_requests_per_minute",
        "fallback_tokens_per_minute",
        "fallback_requests_per_minute",
        mode="before",
    )
    @classmethod
    def _empty_means_unset(cls, value: object) -> object:
        """An empty value in .env means "not set" (no reasoning option, no limit)."""
        return None if value == "" else value

    def primary_provider(self) -> ProviderConfig:
        return self._provider_config("llm", self.llm_provider)

    def fallback_provider_config(self) -> ProviderConfig | None:
        """None when no fallback provider is configured."""
        if self.fallback_provider is None:
            return None
        return self._provider_config("fallback", self.fallback_provider)

    def _provider_config(self, role: Role, name: ProviderName) -> ProviderConfig:
        api_key = getattr(self, f"{name}_api_key")
        if api_key is None:
            raise MissingApiKeyError(f"set {name.upper()}_API_KEY in .env to use {name}")
        return ProviderConfig(
            name=name,
            base_url=PROVIDER_BASE_URLS[name],
            api_key=api_key,
            model=getattr(self, f"{role}_model"),
            input_usd_per_million_tokens=getattr(self, f"{role}_input_usd_per_million_tokens"),
            output_usd_per_million_tokens=getattr(self, f"{role}_output_usd_per_million_tokens"),
            reasoning_effort=getattr(self, f"{role}_reasoning_effort"),
            strict_schema=getattr(self, f"{role}_strict_schema"),
            tokens_per_minute=getattr(self, f"{role}_tokens_per_minute"),
            requests_per_minute=getattr(self, f"{role}_requests_per_minute"),
        )
