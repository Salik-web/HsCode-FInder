"""Application settings, read from the environment and `.env`."""

from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["gemini", "openrouter"]

# Every provider speaks the OpenAI chat API, so adding one is a base URL and a key setting.
PROVIDER_BASE_URLS: dict[ProviderName, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "openrouter": "https://openrouter.ai/api/v1",
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


class Settings(BaseSettings):
    # extra="ignore" because .env also holds POSTGRES_* values for docker-compose.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    cache_dir: Path = Path(".cache/llm")
    reports_dir: Path = Path("evals/reports")

    gemini_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None

    llm_provider: ProviderName = "gemini"
    llm_model: str = "gemini-3.5-flash"
    llm_input_usd_per_million_tokens: Decimal = Decimal(0)
    llm_output_usd_per_million_tokens: Decimal = Decimal(0)

    fallback_provider: ProviderName | None = "openrouter"
    fallback_model: str = "nvidia/nemotron-3.5-super-vl:free"
    fallback_input_usd_per_million_tokens: Decimal = Decimal(0)
    fallback_output_usd_per_million_tokens: Decimal = Decimal(0)

    llm_timeout_seconds: float = 120.0
    # The OpenAI client retries rate limits, timeouts and 5xx with exponential backoff.
    llm_max_retries: int = 3
    llm_max_output_tokens: int = 16_000
    # Free tiers allow a few requests a minute, so only a little concurrency pays off.
    llm_concurrency: int = 2

    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    def primary_provider(self) -> ProviderConfig:
        return self._provider_config(
            self.llm_provider,
            self.llm_model,
            self.llm_input_usd_per_million_tokens,
            self.llm_output_usd_per_million_tokens,
        )

    def fallback_provider_config(self) -> ProviderConfig | None:
        """None when no fallback provider is configured."""
        if self.fallback_provider is None:
            return None
        return self._provider_config(
            self.fallback_provider,
            self.fallback_model,
            self.fallback_input_usd_per_million_tokens,
            self.fallback_output_usd_per_million_tokens,
        )

    def _provider_config(
        self, name: ProviderName, model: str, input_price: Decimal, output_price: Decimal
    ) -> ProviderConfig:
        api_key = {"gemini": self.gemini_api_key, "openrouter": self.openrouter_api_key}[name]
        if api_key is None:
            raise MissingApiKeyError(f"set {name.upper()}_API_KEY in .env to use {name}")
        return ProviderConfig(
            name=name,
            base_url=PROVIDER_BASE_URLS[name],
            api_key=api_key,
            model=model,
            input_usd_per_million_tokens=input_price,
            output_usd_per_million_tokens=output_price,
        )
