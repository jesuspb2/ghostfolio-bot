"""Application configuration loaded from environment variables."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
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

    # IBKR Flex Web Service (read-only Activity Flex Query)
    ibkr_flex_token: SecretStr | None = None
    ibkr_flex_query_id: str | None = None
    ibkr_trade_confirmation_query_id: str | None = None
    ibkr_flex_lookback_days: int = Field(default=365, ge=1, le=365)
    # Destination account: explicit UUID takes precedence over the account name.
    ibkr_ghostfolio_account_id: str | None = None
    ibkr_ghostfolio_account_name: str = "Interactive Brokers"

    # Import mode: "direct" = POST to Ghostfolio; "json" = send JSON file for manual import
    import_mode: Literal["direct", "json"] = "json"

    # Backup
    backup_storage: Literal["telegram", "local"] = "telegram"
    backup_local_dir: Path = Path("backups")
    # Schedule: daily | weekly | monthly | quarterly  (or leave empty to disable)
    backup_schedule: str | None = None
    # Optional encryption: generate a key with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    backup_encryption_key: str | None = None

    # Logging
    log_level: str = "INFO"

    @field_validator("telegram_allowed_users", mode="before")
    @classmethod
    def parse_allowed_users(cls, v: str | list[int] | int) -> list[int]:
        if isinstance(v, int):
            return [v]
        if isinstance(v, str):
            if not v.strip():
                return []
            return [int(uid.strip()) for uid in v.split(",")]
        return v

    @field_validator("backup_schedule", mode="before")
    @classmethod
    def parse_backup_schedule(cls, v: str | None) -> str | None:
        presets: dict[str, str] = {
            "daily":     "0 2 * * *",
            "weekly":    "0 2 * * 1",
            "monthly":   "0 2 1 * *",
            "quarterly": "0 2 1 1,4,7,10 *",
        }
        if isinstance(v, str):
            normalized = v.strip().lower()
            return presets.get(normalized, v.strip()) or None
        return v

    @field_validator("ghostfolio_url", mode="after")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


settings = Settings()  # type: ignore[call-arg]
