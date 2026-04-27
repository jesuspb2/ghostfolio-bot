"""Handler for /import — upload a broker CSV and import into Ghostfolio.

Flow:
  /import → broker selection → CSV upload → account selection → preview → confirm → import
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.config import settings
from bot.ghostfolio_client import (
    GhostfolioClient,
    GhostfolioError,
    build_manual_symbol_map,
    deduplicate_activities,
    resolve_manual_symbols,
)
from bot.parsers import auto_detect_parser, get_all_parsers, get_parser
from bot.utils.auth import restricted

logger = logging.getLogger(__name__)

SELECT_BROKER, UPLOAD_FILE, SELECT_ACCOUNT, IMPORT_CONFIRM = range(4)

_ACCT_PREFIX = "acct:"
_CONFIRM_CB = "import_confirm"
_CANCEL_CB = "import_cancel"


@restricted
async def import_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: show available brokers."""
    if not update.message:
        return ConversationHandler.END

    parsers = get_all_parsers()
    if not parsers:
        await update.message.reply_text("No parsers available yet.")
        return ConversationHandler.END

    broker_names = [p.name for p in (cls() for cls in parsers.values())]
    keyboard = [broker_names[i : i + 2] for i in range(0, len(broker_names), 2)]
    keyboard.append(["Auto-detect"])

    await update.message.reply_text(
        "Which broker is this CSV from?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True),
    )
    return SELECT_BROKER


async def import_select_broker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store selected broker, ask for CSV file."""
    if not update.message:
        return ConversationHandler.END

    text = update.message.text.strip()

    if text == "Auto-detect":
        context.user_data["import_broker"] = "auto"
    else:
        parsers = get_all_parsers()
        matched = None
        for slug, cls in parsers.items():
            if cls().name == text:
                matched = slug
                break

        if not matched:
            await update.message.reply_text("Unknown broker. Try again or choose Auto-detect.")
            return SELECT_BROKER

        context.user_data["import_broker"] = matched

    await update.message.reply_text(
        "Send me the CSV file.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return UPLOAD_FILE


async def import_receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive CSV, parse it, then show account selection."""
    if not update.message or not update.message.document:
        if update.message:
            await update.message.reply_text("Please send a CSV file as a document attachment.")
        return UPLOAD_FILE

    doc = update.message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".csv"):
        await update.message.reply_text("Please send a .csv file.")
        return UPLOAD_FILE

    await update.message.reply_text(f"Processing `{doc.file_name}`…", parse_mode="Markdown")

    tg_file = await doc.get_file(read_timeout=30)
    file_bytes = await tg_file.download_as_bytearray(read_timeout=30)
    csv_content = file_bytes.decode("utf-8-sig")

    broker_slug = context.user_data.get("import_broker", "auto")

    try:
        if broker_slug == "auto":
            parser = auto_detect_parser(csv_content)
            if not parser:
                await update.message.reply_text(
                    "Could not auto-detect the broker format. "
                    "Try /import again and select the broker manually."
                )
                return ConversationHandler.END
            await update.message.reply_text(
                f"Detected format: *{parser.name}*", parse_mode="Markdown"
            )
        else:
            parser = get_parser(broker_slug)

        activities = parser.parse(csv_content)

        if not activities:
            await update.message.reply_text("No importable activities found in this CSV.")
            return ConversationHandler.END

        # Store parsed activities (without accountId yet) for the next step
        context.user_data["pending_activities"] = activities
        context.user_data["pending_parser_name"] = parser.name

        # Fetch Ghostfolio accounts to let the user choose
        async with GhostfolioClient(
            settings.ghostfolio_url, settings.ghostfolio_access_token
        ) as gf:
            accounts = await gf.get_accounts()

        buttons: list[list[InlineKeyboardButton]] = []
        row: list[InlineKeyboardButton] = []

        for acc in accounts:
            acc_id = acc.get("id") or acc.get("accountId", "")
            acc_name = acc.get("name", acc_id)
            acc_currency = acc.get("currency", "")
            label = f"{acc_name} ({acc_currency})" if acc_currency else acc_name
            btn = InlineKeyboardButton(label, callback_data=f"{_ACCT_PREFIX}{acc_id}")
            row.append(btn)
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)

        # Fallback button using the account from config
        if settings.ghostfolio_account_id:
            buttons.append([
                InlineKeyboardButton(
                    "Default account (from config)",
                    callback_data=f"{_ACCT_PREFIX}default",
                )
            ])

        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data=_CANCEL_CB)])

        if not buttons or (len(buttons) == 1 and buttons[0][0].callback_data == _CANCEL_CB):
            await update.message.reply_text(
                "No accounts found in Ghostfolio. "
                "Please create an account first, then retry."
            )
            return ConversationHandler.END

        await update.message.reply_text(
            f"Found *{len(activities)}* activities. Select the destination account:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return SELECT_ACCOUNT

    except ValueError as e:
        await update.message.reply_text(f"Parse error: {e}")
    except GhostfolioError as e:
        logger.error("Ghostfolio error during file processing: %s", e)
        await update.message.reply_text(f"Ghostfolio error: {e.detail}")
    except Exception:
        logger.exception("Unexpected error in /import file step")
        await update.message.reply_text("Unexpected error processing the CSV.")

    _clear_import_state(context)
    return ConversationHandler.END


async def import_select_account_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle account selection, then show dedup preview."""
    query = update.callback_query
    await query.answer()

    if query.data == _CANCEL_CB:
        _clear_import_state(context)
        await query.edit_message_text("Import cancelled.")
        return ConversationHandler.END

    raw_account_id = query.data.removeprefix(_ACCT_PREFIX)
    account_id = (
        settings.ghostfolio_account_id
        if raw_account_id == "default"
        else raw_account_id
    )

    if not account_id:
        await query.edit_message_text(
            "No account ID available. Set GHOSTFOLIO_ACCOUNT_ID in your config."
        )
        return ConversationHandler.END

    activities = context.user_data.pop("pending_activities", None)
    parser_name = context.user_data.get("pending_parser_name", "unknown")

    if not activities:
        await query.edit_message_text("Session expired. Please run /import again.")
        return ConversationHandler.END

    try:
        activity_dicts = [a.to_dict(account_id) for a in activities]

        async with GhostfolioClient(
            settings.ghostfolio_url, settings.ghostfolio_access_token
        ) as gf:
            existing = await gf.get_orders()

        existing_list = (
            existing.get("activities", []) if isinstance(existing, dict) else existing or []
        )

        symbol_map = build_manual_symbol_map(existing_list)
        resolve_manual_symbols(activity_dicts, symbol_map)

        to_import = deduplicate_activities(activity_dicts, existing_list)
        skipped = len(activity_dicts) - len(to_import)

        if not to_import:
            await query.edit_message_text(
                f"All {len(activity_dicts)} activities already exist in Ghostfolio. Nothing to import."
            )
            _clear_import_state(context)
            return ConversationHandler.END

        # Build preview (up to 20 rows)
        lines = [
            f"Found *{len(activity_dicts)}* activities in CSV",
            f"Existing in Ghostfolio: {len(existing_list)}",
            f"Duplicates skipped: {skipped}",
            f"*New to import: {len(to_import)}*\n",
        ]
        for a in to_import[:20]:
            if a["type"] in ("DIVIDEND", "FEE", "INTEREST"):
                amount = f"{a['unitPrice']:.4f} {a['currency']}"
            else:
                amount = f"{a['quantity']} × {a['symbol']}"
            lines.append(f"`{a['date'][:10]}` {a['type']:9s} {amount}")
        if len(to_import) > 20:
            lines.append(f"_…and {len(to_import) - 20} more_")

        context.user_data["pending_import"] = to_import
        context.user_data["pending_parser_name"] = parser_name

        keyboard = [[
            InlineKeyboardButton(f"✅ Import {len(to_import)}", callback_data=_CONFIRM_CB),
            InlineKeyboardButton("❌ Cancel", callback_data=_CANCEL_CB),
        ]]
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return IMPORT_CONFIRM

    except GhostfolioError as e:
        logger.error("Ghostfolio error during dedup: %s", e)
        await query.edit_message_text(f"Ghostfolio error: {e.detail}")
    except Exception:
        logger.exception("Unexpected error in account selection step")
        await query.edit_message_text("Unexpected error. Please try /import again.")

    _clear_import_state(context)
    return ConversationHandler.END


async def import_confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle confirm/cancel inline button press."""
    query = update.callback_query
    await query.answer()

    if query.data == _CANCEL_CB:
        _clear_import_state(context)
        await query.edit_message_text("Import cancelled.")
        return ConversationHandler.END

    to_import = context.user_data.pop("pending_import", None)
    parser_name = context.user_data.pop("pending_parser_name", "unknown")
    context.user_data.pop("import_broker", None)

    if not to_import:
        await query.edit_message_text("No pending import found.")
        return ConversationHandler.END

    try:
        async with GhostfolioClient(
            settings.ghostfolio_url, settings.ghostfolio_access_token
        ) as gf:
            created = await gf.import_activities(to_import)

        types_count: dict[str, int] = {}
        for a in to_import:
            types_count[a["type"]] = types_count.get(a["type"], 0) + 1

        summary_lines = [f"Imported *{created}* activities from *{parser_name}*:\n"]
        for t, c in sorted(types_count.items()):
            summary_lines.append(f"  {t}: {c}")

        if created < len(to_import):
            rejected = len(to_import) - created
            summary_lines.append(
                f"\n⚠️ *{rejected} activities rejected by Ghostfolio* — "
                "check that your account ID, symbol, and dataSource are valid."
            )
        else:
            summary_lines.append("\n_Check Portfolio → Activities in Ghostfolio to verify._")

        await query.edit_message_text("\n".join(summary_lines), parse_mode="Markdown")

    except GhostfolioError as e:
        logger.error("Import API error: %s", e)
        await query.edit_message_text(f"Ghostfolio import failed: {e.detail}")
    except Exception:
        logger.exception("Unexpected error confirming import")
        await query.edit_message_text("Unexpected error during import.")

    return ConversationHandler.END


async def import_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the import flow via /cancel command."""
    if update.message:
        await update.message.reply_text("Import cancelled.", reply_markup=ReplyKeyboardRemove())
    _clear_import_state(context)
    return ConversationHandler.END


def _clear_import_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in ("import_broker", "pending_activities", "pending_import", "pending_parser_name"):
        context.user_data.pop(key, None)


def build_import_conversation() -> ConversationHandler:
    """Build and return the ConversationHandler for /import."""
    return ConversationHandler(
        entry_points=[CommandHandler("import", import_start)],
        states={
            SELECT_BROKER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, import_select_broker),
            ],
            UPLOAD_FILE: [
                MessageHandler(filters.Document.ALL, import_receive_file),
                MessageHandler(filters.TEXT & ~filters.COMMAND, import_receive_file),
            ],
            SELECT_ACCOUNT: [
                CallbackQueryHandler(
                    import_select_account_cb,
                    pattern=f"^({_ACCT_PREFIX}|{_CANCEL_CB})",
                ),
            ],
            IMPORT_CONFIRM: [
                CallbackQueryHandler(
                    import_confirm_cb,
                    pattern=f"^({_CONFIRM_CB}|{_CANCEL_CB})$",
                ),
            ],
        },
        fallbacks=[CommandHandler("cancel", import_cancel)],
    )
