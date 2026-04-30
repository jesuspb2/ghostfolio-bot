"""Backup storage providers: local filesystem and Telegram."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from telegram import Bot

logger = logging.getLogger(__name__)


def make_filename(encrypted: bool = False) -> str:
    ts = datetime.now(tz=UTC).strftime("%Y-%m-%d_%H%M%S")
    suffix = ".json.gz.enc" if encrypted else ".json.gz"
    return f"ghostfolio_backup_{ts}{suffix}"


class LocalBackupProvider:
    def __init__(self, backup_dir: Path) -> None:
        self.backup_dir = backup_dir
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def save(self, data: bytes, filename: str) -> Path:
        dest = self.backup_dir / filename
        dest.write_bytes(data)
        logger.info("Backup saved locally: %s (%d bytes)", dest, len(data))
        return dest

    def list(self) -> list[Path]:
        return sorted(self.backup_dir.glob("ghostfolio_backup_*"), reverse=True)


class TelegramBackupProvider:
    async def save(self, bot: Bot, chat_id: int, data: bytes, filename: str) -> None:
        await bot.send_document(
            chat_id=chat_id,
            document=data,
            filename=filename,
            caption=f"Ghostfolio backup — {filename}",
        )
        logger.info("Backup sent to Telegram chat %d: %s (%d bytes)", chat_id, filename, len(data))


def encrypt(data: bytes, key: str) -> bytes:
    """Encrypt data with a Fernet key. Key must be a valid Fernet key string."""
    from cryptography.fernet import Fernet

    return Fernet(key.encode()).encrypt(data)
