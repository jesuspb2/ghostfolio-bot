"""APScheduler integration for automatic periodic backups."""

from __future__ import annotations

import gzip
import json
import logging
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]
from telegram.ext import Application

from bot.backup.storage import LocalBackupProvider, TelegramBackupProvider, encrypt, make_filename
from bot.config import Settings
from bot.ghostfolio_client import GhostfolioClient

logger = logging.getLogger(__name__)

_SCHEDULER_KEY = "_backup_scheduler"


async def _run_backup(app: Application[Any, Any, Any, Any, Any, Any], cfg: Settings) -> None:
    try:
        async with GhostfolioClient(cfg.ghostfolio_url, cfg.ghostfolio_access_token) as gf:
            data = await gf.export_data()
    except Exception:
        logger.exception("Scheduled backup failed: could not fetch Ghostfolio export")
        return

    encrypted = cfg.backup_encryption_key is not None
    payload = gzip.compress(json.dumps(data).encode())
    if encrypted:
        payload = encrypt(payload, cfg.backup_encryption_key)  # type: ignore[arg-type]
    filename = make_filename(encrypted=encrypted)

    if cfg.backup_storage == "local":
        LocalBackupProvider(cfg.backup_local_dir).save(payload, filename)

    if cfg.backup_storage == "telegram":
        for chat_id in cfg.telegram_allowed_users:
            try:
                await TelegramBackupProvider().save(app.bot, chat_id, payload, filename)
            except Exception:
                logger.exception("Could not send scheduled backup to chat %d", chat_id)


def start_scheduler(app: Application[Any, Any, Any, Any, Any, Any], cfg: Settings) -> None:
    if not cfg.backup_schedule:
        return

    scheduler = AsyncIOScheduler()
    trigger = CronTrigger.from_crontab(cfg.backup_schedule)
    scheduler.add_job(lambda: app.create_task(_run_backup(app, cfg)), trigger)
    scheduler.start()
    app.bot_data[_SCHEDULER_KEY] = scheduler
    logger.info("Backup scheduler started with cron: %s", cfg.backup_schedule)


def stop_scheduler(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    scheduler: AsyncIOScheduler | None = app.bot_data.get(_SCHEDULER_KEY)
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Backup scheduler stopped")
