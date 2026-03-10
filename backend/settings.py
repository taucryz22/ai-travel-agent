from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # General
    mock_mode: bool = Field(default=False, alias="MOCK_MODE")
    llm_enabled: bool = Field(default=True, alias="LLM_ENABLED")

    # Local LLM via Ollama
    ollama_enabled: bool = Field(default=True, alias="OLLAMA_ENABLED")
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="qwen3:8b", alias="OLLAMA_MODEL")

    # OpenAI (optional fallback / legacy)
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-5.4", alias="OPENAI_MODEL")

    # HTTP / retry
    http_timeout_seconds: float = 60.0
    retry_attempts: int = 0
    cache_ttl_seconds: int = 60 * 60

    # Paths / content
    data_path: Path = Path("data")
    wikivoyage_lang: str = Field(default="en", alias="WIKIVOYAGE_LANG")

    # CORS
    backend_cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        alias="BACKEND_CORS_ORIGINS",
    )

    # Nominatim / OSM
    nominatim_base_url: str = Field(
        default="https://nominatim.openstreetmap.org",
        alias="NOMINATIM_BASE_URL",
    )
    nominatim_user_agent: str = Field(
        default="ai-travel-agent-hse/1.0 (contact: your-email@example.com)",
        alias="NOMINATIM_USER_AGENT",
    )
    nominatim_email: str | None = Field(default=None, alias="NOMINATIM_EMAIL")

    # OpenRouteService
    openrouteservice_base_url: str = Field(
        default="https://api.openrouteservice.org",
        alias="OPENROUTESERVICE_BASE_URL",
    )
    openrouteservice_api_key: str | None = Field(
        default=None,
        alias="OPENROUTESERVICE_API_KEY",
    )

    @field_validator("data_path", mode="before")
    @classmethod
    def _coerce_data_path(cls, v):
        if isinstance(v, Path):
            return v
        if not v:
            return Path("data")
        return Path(v)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
