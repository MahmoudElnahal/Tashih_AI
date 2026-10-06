"""Settings, read from environment variables (see ``.env.example``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    log_level: str = "INFO"

    # Hadith sources, tried in order. Known names: dorar, offline.
    sources: str = "dorar,offline"
    dorar_base_url: str = "https://dorar.net/dorar_api.json"
    dorar_timeout_seconds: float = 10.0
    dorar_retry_backoff_seconds: float = 0.5
    # majority = verdict given by most scholars; first = first definite result.
    dorar_primary_rule: str = "majority"
    offline_data_dir: Path = Path("data/hadith")
    offline_base_url: str = "https://cdn.jsdelivr.net/gh/fawazahmed0/hadith-api@1/editions/"
    offline_download_timeout_seconds: float = 180.0

    # OpenAI (optional). Empty key disables every LLM call.
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_timeout_seconds: float = 8.0

    # Aladhan (prayer times / qibla).
    aladhan_base_url: str = "https://api.aladhan.com/v1"
    aladhan_timeout_seconds: float = 10.0
    prayer_default_method: int = 5

    # Cross-cutting.
    cors_origins: str = "*"
    api_keys: str = ""
    rate_limit_per_minute: int = Field(default=60, ge=0)
    cache_ttl_seconds: int = Field(default=3600, ge=0)
    cache_max_entries: int = Field(default=1000, ge=1)

    @property
    def source_names(self) -> list[str]:
        return [name.lower() for name in _split(self.sources)]

    @property
    def cors_origin_list(self) -> list[str]:
        return _split(self.cors_origins)

    @property
    def api_key_set(self) -> frozenset[str]:
        return frozenset(_split(self.api_keys))


@lru_cache
def get_settings() -> Settings:
    return Settings()
