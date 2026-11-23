"""Application settings, read from the environment and `.env`."""

from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from pydantic import BaseModel, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["gemini", "openrouter", "groq"]
# The settings prefix for each model's options: extraction, its fallback, HS reranking.
Role = Literal["llm", "fallback", "hs"]
# json_schema sends our schema to the provider; json_object only asks for a JSON object,
# and the prompt names the keys. Our own validation applies either way.
ResponseFormat = Literal["json_schema", "json_object"]

# Every provider speaks the OpenAI chat API, so adding one is a base URL and a key setting.
PROVIDER_BASE_URLS: dict[ProviderName, str] = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
}


class MissingApiKeyError(RuntimeError):
    """The configured provider has no API key in the environment."""


class MissingDatabaseConfigError(RuntimeError):
    """Neither DATABASE_URL nor the docker-compose password is set."""


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
    # Ask the provider to enforce the JSON schema itself. Our Pydantic validation applies
    # either way; on Groq strict mode often returns an empty or truncated object instead.
    strict_schema: bool
    response_format: ResponseFormat
    # Client-side throttling to stay under the provider's per-minute limits. None: no limit.
    tokens_per_minute: int | None
    output_tokens_per_minute: int | None
    requests_per_minute: int | None
    # Per-call output cap. Thinking models spend output tokens on reasoning too.
    max_output_tokens: int


class Settings(BaseSettings):
    # extra="ignore" because .env also holds POSTGRES_* values for docker-compose.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    # Uploaded documents and their page images (gitignored: real client documents).
    storage_dir: Path = Path("storage")
    # Versions the API and worker run; evals choose their own on the command line.
    invoice_prompt_version: str = "v3"
    hs_query_prompt_version: str = "v1"
    hs_rerank_prompt_version: str = "v1"
    # Seconds the worker waits before looking for queued documents again.
    worker_poll_seconds: float = 2.0
    # Where the review UI finds the API.
    api_url: str = "http://127.0.0.1:8000"

    # Public demo (Hugging Face Space). Groq limits are per organization, so these caps
    # are what keep visitors from spending the project's daily quota.
    demo_mode: bool = False
    demo_session_uploads: int = 2
    demo_visitor_daily_uploads: int = 3
    demo_daily_uploads: int = 10
    demo_hs_lines: int = 5  # lines per document that get HS suggestions
    demo_retention_minutes: int = 60
    # Committed LLM answers for the sample invoices: they are served with no API call.
    demo_cache_dir: Path = Path("demo/llm_cache")
    demo_samples_dir: Path = Path("demo/samples")
    # Salt for hashing visitor IPs; the raw IP is never stored.
    demo_uploader_salt: SecretStr = SecretStr("local-demo-salt")
    cache_dir: Path = Path(".cache/llm")
    # Store live LLM answers. Off in the public demo: an answer holds a visitor's extracted
    # invoice data, which must go when the upload does. Offline (cache-only) clients, such
    # as the demo's sample seeding, always read their cache.
    llm_cache_enabled: bool = True
    reports_dir: Path = Path("evals/reports")

    gemini_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None

    llm_provider: ProviderName = "groq"
    # The only Groq model that accepts images (checked 2026-11-11); qwen3.6-27b isn't served.
    llm_model: str = "qwen/qwen3.8-27b"
    llm_reasoning_effort: str | None = "none"
    llm_strict_schema: bool = False
    # Groq pastes a json_schema into the prompt (about 1,000 tokens a call) and doesn't
    # enforce it; json_object plus the key list in prompt v3 costs less under the daily cap.
    llm_response_format: ResponseFormat = "json_object"
    # Groq free tier for this model: 8,000 tokens and 1,000 output tokens a minute, 1,000
    # requests a day. The output cap also limits a single call.
    llm_tokens_per_minute: int | None = 8_000
    llm_output_tokens_per_minute: int | None = 1_000
    llm_requests_per_minute: int | None = 30
    llm_input_usd_per_million_tokens: Decimal = Decimal(0)
    llm_output_usd_per_million_tokens: Decimal = Decimal(0)

    fallback_provider: ProviderName | None = "groq"
    fallback_model: str = "qwen/qwen3.8-27b"
    fallback_reasoning_effort: str | None = "none"
    fallback_strict_schema: bool = False
    fallback_response_format: ResponseFormat = "json_object"
    fallback_tokens_per_minute: int | None = 8_000
    fallback_output_tokens_per_minute: int | None = 1_000
    fallback_requests_per_minute: int | None = 30
    fallback_input_usd_per_million_tokens: Decimal = Decimal(0)
    fallback_output_usd_per_million_tokens: Decimal = Decimal(0)

    # HS reranking: text only, its own Groq per-model daily quota, never a vision call.
    hs_provider: ProviderName = "groq"
    hs_model: str = "openai/gpt-oss-120b"
    hs_reasoning_effort: str | None = "low"
    hs_strict_schema: bool = False
    hs_response_format: ResponseFormat = "json_object"
    # Groq free tier for gpt-oss-120b: 8,000 tokens and 30 requests a minute.
    hs_tokens_per_minute: int | None = 8_000
    hs_output_tokens_per_minute: int | None = None
    hs_requests_per_minute: int | None = 30
    hs_input_usd_per_million_tokens: Decimal = Decimal(0)
    hs_output_usd_per_million_tokens: Decimal = Decimal(0)
    # Reasoning counts against the cap; low effort uses a few hundred tokens.
    hs_max_output_tokens: int = 2_000

    # Local sentence-transformers model; 384 dimensions, as tariff_codes.embedding.
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # Postgres + pgvector. DATABASE_URL (Neon, Supabase) overrides the docker-compose values.
    database_url: SecretStr | None = None
    postgres_user: str = "tradedoc"
    postgres_password: SecretStr | None = None
    postgres_db: str = "tradedoc"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432

    llm_timeout_seconds: float = 120.0
    # The OpenAI client retries 5xx, timeouts and (as a last resort) 429 with backoff.
    llm_max_retries: int = 3
    # Groq's output limit per minute is also its per-call ceiling. Thinking models (Gemini)
    # spend output tokens on reasoning: raise this to 16000 for them.
    llm_max_output_tokens: int = 1_000
    fallback_max_output_tokens: int = 1_000
    # Free tiers allow a few requests a minute, so only a little concurrency pays off.
    llm_concurrency: int = 2

    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    @field_validator(
        "llm_reasoning_effort",
        "fallback_reasoning_effort",
        "llm_tokens_per_minute",
        "llm_output_tokens_per_minute",
        "llm_requests_per_minute",
        "fallback_tokens_per_minute",
        "fallback_output_tokens_per_minute",
        "fallback_requests_per_minute",
        "hs_reasoning_effort",
        "hs_tokens_per_minute",
        "hs_output_tokens_per_minute",
        "hs_requests_per_minute",
        "database_url",
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

    def hs_provider_config(self) -> ProviderConfig:
        return self._provider_config("hs", self.hs_provider)

    def sqlalchemy_url(self) -> str:
        """DATABASE_URL when set, else the docker-compose database on this machine."""
        if self.database_url is not None:
            return self.database_url.get_secret_value()
        if self.postgres_password is None:
            raise MissingDatabaseConfigError("set DATABASE_URL, or POSTGRES_PASSWORD, in .env")
        password = quote_plus(self.postgres_password.get_secret_value())
        return (
            f"postgresql+psycopg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

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
            response_format=getattr(self, f"{role}_response_format"),
            tokens_per_minute=getattr(self, f"{role}_tokens_per_minute"),
            output_tokens_per_minute=getattr(self, f"{role}_output_tokens_per_minute"),
            requests_per_minute=getattr(self, f"{role}_requests_per_minute"),
            max_output_tokens=getattr(self, f"{role}_max_output_tokens"),
        )
