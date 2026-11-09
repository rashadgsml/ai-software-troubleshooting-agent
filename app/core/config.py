from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven config (the FastAPI equivalent of Django's settings.py)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://troubleshooter:troubleshooter@localhost:5433/troubleshooter"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    api_key: str = "change-me"  # clients send it as the X-API-Key header
    app_env: str = "dev"  # "production" refuses to start with a weak API key

    # abuse / cost controls
    rate_limit_per_minute: int = 60  # all authenticated requests, per API key
    investigation_rate_per_minute: int = 5  # starting investigations costs money
    failed_auth_per_minute: int = 10  # per client IP
    max_concurrent_investigations: int = 3

    # timeouts
    anthropic_timeout_s: float = 60.0  # per Claude request
    anthropic_max_retries: int = 3
    db_statement_timeout_ms: int = 15_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
