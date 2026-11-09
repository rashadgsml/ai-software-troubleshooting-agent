from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven config (the FastAPI equivalent of Django's settings.py)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://troubleshooter:troubleshooter@localhost:5432/troubleshooter"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"
    api_key: str = "change-me"  # used for API auth in the hardening phase


@lru_cache
def get_settings() -> Settings:
    return Settings()
