"""Handler for /backup and /restore commands."""

from __future__ import annotations

import gzip
import json
import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.backup.storage import LocalBackupProvider, TelegramBackupProvider, encrypt, make_filename
from bot.config import settings
from bot.ghostfolio_client import GhostfolioClient
from bot.utils.auth import restricted

logger = logging.getLogger(__name__)


async def _do_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg:
        return

    await msg.chat.send_action(ChatAction.UPLOAD_DOCUMENT)

    try:
        async with GhostfolioClient(
            settings.ghostfolio_url, settings.ghostfolio_access_token
        ) as gf:
            data = await gf.export_data()
    except Exception as exc:
        logger.exception("Failed to fetch Ghostfolio export")
        await msg.reply_text(f"❌ Export failed: {exc}")
        return

    encrypted = settings.backup_encryption_key is not None
    payload = gzip.compress(json.dumps(data).encode())
    if encrypted:
        payload = encrypt(payload, settings.backup_encryption_key)  # type: ignore[arg-type]
    filename = make_filename(encrypted=encrypted)
    size_kb = len(payload) / 1024

    enc_note = " 🔒 encrypted" if encrypted else ""
    results: list[str] = []

    if settings.backup_storage == "local":
        provider = LocalBackupProvider(settings.backup_local_dir)
        path = provider.save(payload, filename)
        results.append(f"💾 Saved to `{path}`{enc_note}")

    if settings.backup_storage == "telegram":
        await TelegramBackupProvider().save(context.bot, msg.chat_id, payload, filename)
        results.append(f"📨 Sent as Telegram document{enc_note}")

    summary = "\n".join(results)
    await msg.reply_text(
        f"✅ Backup complete — `{filename}` ({size_kb:.1f} KB)\n\n{summary}",
        parse_mode="Markdown",
    )


@restricted
async def backup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _do_backup(update, context)


@restricted
async def restore_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    await update.message.chat.send_action(ChatAction.TYPING)

    if settings.backup_storage == "local":
        provider = LocalBackupProvider(settings.backup_local_dir)
        files = provider.list()
        if files:
            lines = [f"`{f.name}`" for f in files[:10]]
            text = "📂 *Local backups* (most recent first):\n\n" + "\n".join(lines)
            if len(files) > 10:
                text += f"\n_…and {len(files) - 10} more_"
        else:
            text = "No local backups found."
    else:
        text = (
            "Backups are stored in your Telegram chat history.\n"
            "To restore, send a backup file and use /import."
        )

    await update.message.reply_text(text, parse_mode="Markdown")
