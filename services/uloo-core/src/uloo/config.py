# AI生成
"""ULOO Core configuration."""

import re

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ULOO_",
        extra="ignore",
    )

    # Application
    app_name: str = "ULOO Core"
    debug: bool = False
    log_level: str = "INFO"

    # Database
    db_host: str = "localhost"
    db_port: int = 5432
    db_username: str = "lightrag"
    db_password: str = "lightrag123"
    db_database: str = "uloo"
    db_schema: str = "public"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0

    # API
    api_prefix: str = "/api/v1"
    api_token: str = ""
    dify_api_url: str = "http://localhost:5001"
    dify_inner_api_key: SecretStr = SecretStr("")
    # Server-owned Dify app/account context, keyed by workspace; never supplied by a model.
    dify_workspace_callers: dict[str, dict[str, str]] = {}

    # Agno
    agno_enabled: bool = True

    # Model providers (key=provider, value=base_url or empty for default)
    # e.g. {"openai": "https://api.openai.com/v1"}
    # Universal / Simple LLM Configuration (OpenAI-compatible)
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""

    # Embedding Configuration (SiliconFlow / OpenAI-compatible for pgvector hybrid search)
    embedding_base_url: str = ""
    embedding_api_key: SecretStr = SecretStr("")
    embedding_model: str = "BAAI/bge-m3"
    embedding_timeout_seconds: float = 8.0

    model_providers: dict[str, str] = {}
    model_provider_api_keys: dict[str, SecretStr] = {}
    # Optional catalog of selectable model ids per provider, surfaced via capabilities
    # so the UI can render a picker instead of asking the user to type `provider:model-id`.
    # When empty, the planner_model_ref is used to derive at least one selectable model.
    model_catalog: dict[str, list[str]] = {}
    # Aliases mapping Dify workspace provider IDs to ULOO .env provider names.
    # e.g. {"langgenius/deepseek/deepseek": "deepseek"}
    # When a model_ref uses a Dify provider ID, the alias resolves it to the
    # .env-configured provider for credential lookup.  If no alias exists, the
    # raw provider ID is used as the .env key (backward compatible).
    model_provider_aliases: dict[str, str] = {}
    model_timeout_seconds: float = 900
    planner_model_ref: str = ""

    @field_validator("db_schema")
    @classmethod
    def validate_db_schema(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("db_schema must be a simple SQL identifier")
        return value

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_username}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_database}"
        )

    @property
    def sync_database_url(self) -> str:
        return (
            f"postgresql://{self.db_username}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_database}"
        )

    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"


settings = Settings()
