"""Application configuration loaded from environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Telegram
    telegram_bot_token: str
    telegram_allowed_users: list[int] = []

    # Ghostfolio
    ghostfolio_url: str = "http://localhost:3333"
    ghostfolio_access_token: str
    ghostfolio_account_id: str

    # Backup
    backup_provider: str = "local"  # "local" | "telegram"
    backup_local_path: Path = Path("./backups")
    backup_cron: str = "0 3 * * *"

    # Logging
    log_level: str = "INFO"

    @field_validator("telegram_allowed_users", mode="before")
    @classmethod
    def parse_allowed_users(cls, v: str | list[int]) -> list[int]:
        if isinstance(v, str):
            if not v.strip():
                return []
            return [int(uid.strip()) for uid in v.split(",")]
        return v

    @field_validator("ghostfolio_url", mode="after")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


settings = Settings()  # type: ignore[call-arg]
