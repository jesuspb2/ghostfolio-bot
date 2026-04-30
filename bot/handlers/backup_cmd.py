"""Handler for /backup and /restore commands."""

from __future__ import annotations

import gzip
import json
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.backup.storage import LocalBackupProvider, TelegramBackupProvider, encrypt, make_filename
from bot.config import settings
from bot.ghostfolio_client import GhostfolioClient, GhostfolioError, deduplicate_activities
from bot.utils.auth import restricted

logger = logging.getLogger(__name__)

RESTORE_WAIT_FILE, RESTORE_CONFIRM = range(2)
_IMPORT_EXCLUDE = {"id", "createdAt", "updatedAt", "SymbolProfile", "Account", "tags"}
_RESTORE_CONFIRM_CB = "restore_confirm"
_RESTORE_CANCEL_CB = "restore_cancel"


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
async def restore_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END

    await update.message.chat.send_action(ChatAction.TYPING)

    if settings.backup_storage == "local":
        provider = LocalBackupProvider(settings.backup_local_dir)
        files = provider.list()
        if files:
            lines = [f"`{f.name}`" for f in files[:10]]
            hint = "📂 *Local backups* (most recent first):\n\n" + "\n".join(lines)
            if len(files) > 10:
                hint += f"\n_…and {len(files) - 10} more_"
            hint += "\n\nSend the backup file to restore it, or /cancel to abort."
        else:
            hint = "No local backups found. Send a backup file to restore it, or /cancel to abort."
    else:
        hint = (
            "Backups are stored in your Telegram chat history.\n\n"
            "Send me the backup file (`.json.gz` or `.json.gz.enc`) and I'll restore it. "
            "Send /cancel to abort."
        )

    await update.message.reply_text(hint, parse_mode="Markdown")
    return RESTORE_WAIT_FILE


async def restore_receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.document:
        if update.message:
            await update.message.reply_text(
                "Please send a `.json.gz` or `.json.gz.enc` backup file. Send /cancel to abort."
            )
        return RESTORE_WAIT_FILE

    assert context.user_data is not None
    doc = update.message.document
    fname = (doc.file_name or "").lower()

    if not (fname.endswith(".json.gz") or fname.endswith(".json.gz.enc")):
        await update.message.reply_text(
            "Unsupported file. Please send a `.json.gz` or `.json.gz.enc` backup file."
        )
        return RESTORE_WAIT_FILE

    await update.message.reply_text(f"Processing `{doc.file_name}`…", parse_mode="Markdown")
    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        tg_file = await doc.get_file(read_timeout=30)
        raw = bytes(await tg_file.download_as_bytearray(read_timeout=30))

        if fname.endswith(".enc"):
            if not settings.backup_encryption_key:
                await update.message.reply_text(
                    "❌ This backup is encrypted but `BACKUP_ENCRYPTION_KEY` is not set in config.",
                    parse_mode="Markdown",
                )
                return ConversationHandler.END
            from cryptography.fernet import Fernet, InvalidToken
            try:
                raw = Fernet(settings.backup_encryption_key.encode()).decrypt(raw)
            except InvalidToken:
                await update.message.reply_text(
                    "❌ Decryption failed — wrong key or corrupted file."
                )
                return ConversationHandler.END

        payload = json.loads(gzip.decompress(raw))
        activities: list[dict[str, object]] = [
            {k: v for k, v in a.items() if k not in _IMPORT_EXCLUDE}
            for a in payload.get("activities", [])
        ]

        if not activities:
            await update.message.reply_text("No activities found in this backup file.")
            return ConversationHandler.END

        async with GhostfolioClient(
            settings.ghostfolio_url, settings.ghostfolio_access_token
        ) as gf:
            existing = await gf.get_orders()
            existing_list = (
                existing.get("activities", []) if isinstance(existing, dict) else existing or []
            )

        to_import = deduplicate_activities(activities, existing_list)
        skipped = len(activities) - len(to_import)

        if not to_import:
            await update.message.reply_text(
                f"All {len(activities)} activities already exist in Ghostfolio. Nothing to restore."
            )
            return ConversationHandler.END

        context.user_data["restore_activities"] = to_import

        lines = [
            f"*Backup:* `{doc.file_name}`",
            f"Activities in backup: {len(activities)}",
            f"Already in Ghostfolio: {skipped}",
            f"*New to restore: {len(to_import)}*",
        ]
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"✅ Restore {len(to_import)} activities", callback_data=_RESTORE_CONFIRM_CB
            ),
            InlineKeyboardButton("❌ Cancel", callback_data=_RESTORE_CANCEL_CB),
        ]])
        await update.message.reply_text(
            "\n".join(lines), parse_mode="Markdown", reply_markup=keyboard
        )
        return RESTORE_CONFIRM

    except (json.JSONDecodeError, gzip.BadGzipFile, KeyError) as e:
        await update.message.reply_text(f"❌ Could not parse backup file: {e}")
    except GhostfolioError as e:
        await update.message.reply_text(f"❌ Ghostfolio error: {e.detail}")
    except Exception:
        logger.exception("Unexpected error in /restore file step")
        await update.message.reply_text("❌ Unexpected error. Please try /restore again.")

    return ConversationHandler.END


async def restore_confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    assert update.callback_query is not None
    assert context.user_data is not None

    query = update.callback_query
    await query.answer()

    if query.data == _RESTORE_CANCEL_CB:
        context.user_data.pop("restore_activities", None)
        await query.edit_message_text("Restore cancelled.")
        return ConversationHandler.END

    to_import = context.user_data.pop("restore_activities", None)
    if not to_import:
        await query.edit_message_text("Session expired. Please run /restore again.")
        return ConversationHandler.END

    await query.edit_message_text(f"Restoring {len(to_import)} activities…")

    try:
        async with GhostfolioClient(
            settings.ghostfolio_url, settings.ghostfolio_access_token
        ) as gf:
            created, skipped_symbols = await gf.import_activities(to_import)

        lines = [f"✅ Restored *{created}* activities."]
        if skipped_symbols:
            unique = sorted(set(skipped_symbols))
            lines.append(
                f"\n⚠️ *{len(skipped_symbols)} activities skipped* — symbol not found:\n"
                + ", ".join(f"`{s}`" for s in unique)
            )
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown")

    except GhostfolioError as e:
        logger.error("Ghostfolio error during restore: %s", e)
        await query.edit_message_text(f"❌ Ghostfolio error: {e.detail}")
    except Exception:
        logger.exception("Unexpected error confirming restore")
        await query.edit_message_text("❌ Unexpected error during restore.")

    return ConversationHandler.END


async def restore_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text("Restore cancelled.")
    if context.user_data:
        context.user_data.pop("restore_activities", None)
    return ConversationHandler.END


def build_restore_conversation() -> ConversationHandler:  # type: ignore[type-arg]
    return ConversationHandler(
        entry_points=[CommandHandler("restore", restore_start)],
        states={
            RESTORE_WAIT_FILE: [
                MessageHandler(filters.Document.ALL, restore_receive_file),
            ],
            RESTORE_CONFIRM: [
                CallbackQueryHandler(
                    restore_confirm_cb,
                    pattern=f"^({_RESTORE_CONFIRM_CB}|{_RESTORE_CANCEL_CB})$",
                ),
            ],
        },
        fallbacks=[CommandHandler("cancel", restore_cancel)],
        allow_reentry=True,
    )
