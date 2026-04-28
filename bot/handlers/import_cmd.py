"""Handler for /import — upload a broker CSV and import into Ghostfolio.

Flow:
  /import → CSV upload → (auto-detect broker) → account selection → preview → confirm → import
  If auto-detect fails → broker selection keyboard (CSV already stored) → account selection → …

Cancel is available at every step:
  - ReplyKeyboard "❌ Cancel" button during UPLOAD_FILE and SELECT_BROKER
  - Inline "❌ Cancel" button during SELECT_ACCOUNT and IMPORT_CONFIRM
  - /cancel command works at any point
  - /import restarts a stuck conversation (allow_reentry=True)
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from contextlib import asynccontextmanager

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.constants import ChatAction
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
from bot.parsers import auto_detect_parser, auto_detect_parser_binary, get_all_parsers, get_parser
from bot.parsers.symbol_resolver import resolve_symbols
from bot.utils.auth import restricted

logger = logging.getLogger(__name__)

UPLOAD_FILE, SELECT_BROKER, SELECT_ACCOUNT, IMPORT_CONFIRM = range(4)


@asynccontextmanager
async def _typing(chat):
    """Keep sending TYPING action every 4 s until the context exits."""
    async def _loop():
        try:
            while True:
                await chat.send_action(ChatAction.TYPING)
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_loop())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.shield(task)

_ACCT_PREFIX = "acct:"
_CONFIRM_CB = "import_confirm"
_CANCEL_CB = "import_cancel"
_CANCEL_TEXT = "❌ Cancel"


@restricted
async def import_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: ask for the CSV file directly."""
    if not update.message:
        return ConversationHandler.END

    _clear_import_state(context)
    await update.message.chat.send_action(ChatAction.TYPING)
    await update.message.reply_text(
        "Send me the CSV file and I'll detect the broker automatically.\n\nSend /cancel to abort.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return UPLOAD_FILE


async def import_receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive CSV, auto-detect broker, then show account selection."""
    _clear_import_state(context)
    if not update.message or not update.message.document:
        if update.message:
            await update.message.reply_text(
                "Please send a CSV file as a document attachment. Send /cancel to abort."
            )
        return UPLOAD_FILE

    doc = update.message.document
    fname = (doc.file_name or "").lower()
    if not (fname.endswith(".csv") or fname.endswith(".xls")):
        await update.message.reply_text(
            "Please send a .csv or .xls file. Send /cancel to abort."
        )
        return UPLOAD_FILE

    await update.message.reply_text(
        f"Processing `{doc.file_name}`…",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )

    is_xls = fname.endswith(".xls")

    async with _typing(update.message.chat):
        tg_file = await doc.get_file(read_timeout=30)
        file_bytes = bytes(await tg_file.download_as_bytearray(read_timeout=30))

        if is_xls:
            parser = auto_detect_parser_binary(file_bytes)
        else:
            csv_content = file_bytes.decode("utf-8-sig")
            parser = auto_detect_parser(csv_content)

    if not parser:
        keyboard = _broker_keyboard()
        if is_xls:
            context.user_data["file_bytes"] = file_bytes
            context.user_data["is_xls"] = True
        else:
            context.user_data["csv_content"] = csv_content
            context.user_data["is_xls"] = False
        await update.message.reply_text(
            "Could not detect the broker format automatically.\n"
            f"Which broker is this {'XLS' if is_xls else 'CSV'} from? Send /cancel to abort.",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
        )
        return SELECT_BROKER

    await update.message.reply_text(
        f"Detected format: *{parser.name}*", parse_mode="Markdown"
    )
    if is_xls:
        return await _process_file(update, context, file_bytes=file_bytes, broker_slug=parser.slug)
    return await _process_csv(update, context, csv_content, parser.slug)


async def import_select_broker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Fallback: user manually picked a broker after auto-detect failed."""
    if not update.message:
        return ConversationHandler.END

    text = update.message.text.strip()
    parsers = get_all_parsers()
    matched = None
    for slug, cls in parsers.items():
        if cls().name == text:
            matched = slug
            break

    if not matched:
        keyboard = _broker_keyboard()
        await update.message.reply_text(
            "Unknown broker. Please choose one from the list:",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
        )
        return SELECT_BROKER

    context.user_data["import_broker"] = matched
    is_xls = context.user_data.get("is_xls", False)

    if is_xls:
        file_bytes = context.user_data.get("file_bytes")
        if not file_bytes:
            await update.message.reply_text(
                "Session expired. Please run /import again.",
                reply_markup=ReplyKeyboardRemove(),
            )
            _clear_import_state(context)
            return ConversationHandler.END
        return await _process_file(update, context, file_bytes=file_bytes, broker_slug=matched)

    csv_content = context.user_data.get("csv_content")
    if not csv_content:
        await update.message.reply_text(
            "Session expired. Please run /import again.",
            reply_markup=ReplyKeyboardRemove(),
        )
        _clear_import_state(context)
        return ConversationHandler.END

    return await _process_csv(update, context, csv_content, matched)


async def _process_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    file_bytes: bytes,
    broker_slug: str,
) -> int:
    """Parse a binary file (e.g. XLS) with the given broker slug and proceed to account selection."""
    try:
        async with _typing(update.message.chat):
            parser = get_parser(broker_slug)
            activities = parser.parse_binary(file_bytes)

            if not activities:
                await update.message.reply_text(
                    "No importable activities found in this file.",
                    reply_markup=ReplyKeyboardRemove(),
                )
                _clear_import_state(context)
                return ConversationHandler.END

            context.user_data["pending_activities"] = activities
            context.user_data["pending_parser_name"] = parser.name

            async with GhostfolioClient(
                settings.ghostfolio_url, settings.ghostfolio_access_token
            ) as gf:
                accounts = await gf.get_accounts()

        return await _show_account_selection(update, context, activities, accounts)

    except ValueError as e:
        await update.message.reply_text(f"Parse error: {e}", reply_markup=ReplyKeyboardRemove())
    except GhostfolioError as e:
        logger.error("Ghostfolio error during file processing: %s", e)
        await update.message.reply_text(
            f"Ghostfolio error: {e.detail}", reply_markup=ReplyKeyboardRemove()
        )
    except Exception:
        logger.exception("Unexpected error in /import XLS step")
        await update.message.reply_text(
            "Unexpected error processing the file.", reply_markup=ReplyKeyboardRemove()
        )

    _clear_import_state(context)
    return ConversationHandler.END


async def _process_csv(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    csv_content: str,
    broker_slug: str,
) -> int:
    """Parse CSV with the given broker slug and proceed to account selection."""
    try:
        async with _typing(update.message.chat):
            parser = get_parser(broker_slug)
            activities = parser.parse(csv_content)

            if not activities:
                await update.message.reply_text(
                    "No importable activities found in this CSV.",
                    reply_markup=ReplyKeyboardRemove(),
                )
                _clear_import_state(context)
                return ConversationHandler.END

            context.user_data["pending_activities"] = activities
            context.user_data["pending_parser_name"] = parser.name

            async with GhostfolioClient(
                settings.ghostfolio_url, settings.ghostfolio_access_token
            ) as gf:
                accounts = await gf.get_accounts()

        return await _show_account_selection(update, context, activities, accounts)

    except ValueError as e:
        await update.message.reply_text(f"Parse error: {e}", reply_markup=ReplyKeyboardRemove())
    except GhostfolioError as e:
        logger.error("Ghostfolio error during file processing: %s", e)
        await update.message.reply_text(
            f"Ghostfolio error: {e.detail}", reply_markup=ReplyKeyboardRemove()
        )
    except Exception:
        logger.exception("Unexpected error in /import file step")
        await update.message.reply_text(
            "Unexpected error processing the CSV.", reply_markup=ReplyKeyboardRemove()
        )

    _clear_import_state(context)
    return ConversationHandler.END


async def _show_account_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    activities: list,
    accounts: list,
) -> int:
    """Build and send the account selection keyboard. Returns SELECT_ACCOUNT state."""
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

    if settings.ghostfolio_account_id:
        buttons.append([
            InlineKeyboardButton(
                "Default account (from config)",
                callback_data=f"{_ACCT_PREFIX}default",
            )
        ])

    buttons.append([InlineKeyboardButton(_CANCEL_TEXT, callback_data=_CANCEL_CB)])

    if len(buttons) == 1:  # only the cancel button — no accounts found
        await update.message.reply_text(
            "No accounts found in Ghostfolio. "
            "Please create an account first, then retry.",
            reply_markup=ReplyKeyboardRemove(),
        )
        _clear_import_state(context)
        return ConversationHandler.END

    await update.message.reply_text(
        f"Found *{len(activities)}* activities. Select the destination account:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return SELECT_ACCOUNT


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

        # Resolve ISINs → Yahoo Finance tickers for all YAHOO-sourced activities.
        # This is needed for DEGIRO, IBKR, and any future parser that emits raw ISINs.
        await query.edit_message_text("Resolving symbols via Yahoo Finance…")
        async with _typing(query.message.chat):
            activity_dicts, unresolvable_isins = await resolve_symbols(activity_dicts)

        async with GhostfolioClient(
            settings.ghostfolio_url, settings.ghostfolio_access_token
        ) as gf:
            existing = await gf.get_orders(account_id=account_id)

            existing_list = (
                existing.get("activities", []) if isinstance(existing, dict) else existing or []
            )

            symbol_map = build_manual_symbol_map(existing_list)
            resolve_manual_symbols(activity_dicts, symbol_map)

            # Drop activities whose ISINs could not be resolved — Ghostfolio would reject them.
            unresolvable_set = set(unresolvable_isins)
            isin_skipped_activities = [a for a in activity_dicts if a.get("symbol") in unresolvable_set]
            activity_dicts = [a for a in activity_dicts if a.get("symbol") not in unresolvable_set]

            # Drop activities where the Yahoo Finance symbol implies a native currency
            # that doesn't match the recorded one. DEGIRO (and some other brokers) convert
            # LSE/foreign-exchange transactions to the account currency (EUR), but Ghostfolio
            # crashes trying to reconcile e.g. IAG.L (GBp data) with currency=EUR.
            activity_dicts, currency_skipped_activities = _filter_currency_mismatches(activity_dicts)

            # Safety net: drop any activity with an empty or obviously invalid currency.
            # Parsers should guard this themselves, but a stray empty value would cause
            # Ghostfolio to return 400 "must be a valid ISO4217 currency code".
            valid_currency = re.compile(r"^[A-Za-z]{3}$")
            invalid_currency = [
                a for a in activity_dicts if not valid_currency.match(a.get("currency", ""))
            ]
            if invalid_currency:
                bad_currencies = {a.get("currency", "") for a in invalid_currency}
                logger.warning(
                    "Dropping %d activities with invalid currency: %s",
                    len(invalid_currency),
                    bad_currencies,
                )
                activity_dicts = [a for a in activity_dicts if valid_currency.match(a.get("currency", ""))]

            to_import = deduplicate_activities(activity_dicts, existing_list)
            skipped = len(activity_dicts) - len(to_import)

            # Sort chronologically so Ghostfolio never sees a SELL before its BUYs.
            # Out-of-order imports (e.g. newest-first CSVs) cause Ghostfolio to crash
            # when computing portfolio performance on a negative position.
            to_import.sort(key=lambda a: a.get("date", ""))

            if not to_import:
                await query.edit_message_text(
                    f"All {len(activity_dicts)} activities already exist in Ghostfolio. Nothing to import."
                )
                _clear_import_state(context)
                return ConversationHandler.END

            # Validate against Ghostfolio before showing the confirm button.
            await query.edit_message_text("Validating activities against Ghostfolio…")
            validation_errors: list[str] = []
            try:
                async with _typing(query.message.chat):
                    validation_errors = await gf.validate_activities(to_import)
            except Exception:
                logger.exception("Unexpected error during dry-run validation")
                validation_errors = ["Could not reach Ghostfolio for validation."]

        lines = [
            f"Found *{len(activity_dicts)}* activities in CSV",
            f"Existing in Ghostfolio: {len(existing_list)}",
            f"Duplicates skipped: {skipped}",
            f"*New to import: {len(to_import)}*",
        ]
        if validation_errors:
            lines.append("")
            lines.append("⚠️ *Ghostfolio validation errors:*")
            for err in validation_errors:
                lines.append(f"  • `{err}`")
        else:
            lines.append("✅ Validated by Ghostfolio")
        if isin_skipped_activities:
            lines.append(
                f"⚠️ *{len(isin_skipped_activities)} activit{'y' if len(isin_skipped_activities) == 1 else 'ies'} "
                "skipped* (ISIN not found on Yahoo Finance):"
            )
            for a in isin_skipped_activities:
                lines.append(f"  `{a['date'][:10]}` {a['type']:9s} `{a.get('symbol', '?')}`")
        if currency_skipped_activities:
            lines.append(
                f"⚠️ *{len(currency_skipped_activities)} activit{'y' if len(currency_skipped_activities) == 1 else 'ies'} "
                "skipped* (currency mismatch — broker recorded in account currency, "
                "but Yahoo Finance uses a different native currency):"
            )
            for a in currency_skipped_activities:
                lines.append(f"  `{a['date'][:10]}` {a['type']:9s} `{a.get('symbol', '?')}`")
        lines.append("")
        for a in to_import:
            if a["type"] in ("DIVIDEND", "FEE", "INTEREST"):
                amount = f"{a['unitPrice']:.4f} {a['currency']}"
            else:
                amount = f"{a['quantity']} × {a['symbol']}"
            lines.append(f"`{a['date'][:10]}` {a['type']:9s} {amount}")

        context.user_data["pending_import"] = to_import
        context.user_data["pending_parser_name"] = parser_name

        if settings.import_mode == "json":
            confirm_label = f"📥 Download JSON ({len(to_import)} activities)"
        else:
            confirm_label = f"✅ Import {len(to_import)} to Ghostfolio"

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(confirm_label, callback_data=_CONFIRM_CB),
            InlineKeyboardButton(_CANCEL_TEXT, callback_data=_CANCEL_CB),
        ]])
        full_text = "\n".join(lines)
        # Telegram max message length is 4096 chars; split if needed
        if len(full_text) <= 4096:
            await query.edit_message_text(full_text, parse_mode="Markdown", reply_markup=keyboard)
        else:
            chunks = _split_message(full_text, 4096)
            await query.edit_message_text(chunks[0], parse_mode="Markdown")
            if isinstance(query.message, Message):
                for chunk in chunks[1:-1]:
                    await query.message.reply_text(chunk, parse_mode="Markdown")
                await query.message.reply_text(chunks[-1], parse_mode="Markdown", reply_markup=keyboard)
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

    if settings.import_mode == "json":
        await _send_import_json(query, to_import, parser_name)
    else:
        await _do_direct_import(query, to_import, parser_name)

    return ConversationHandler.END


async def _send_import_json(query, to_import: list[dict], parser_name: str) -> None:
    """Export activities as a Ghostfolio-compatible JSON file and send to user."""
    try:
        payload = {"activities": to_import}
        json_bytes = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        buf = io.BytesIO(json_bytes)
        buf.name = f"ghostfolio-import-{parser_name.lower().replace(' ', '-')}.json"

        types_count: dict[str, int] = {}
        for a in to_import:
            types_count[a["type"]] = types_count.get(a["type"], 0) + 1

        caption_lines = [f"*{parser_name}* — {len(to_import)} activities ready to import:\n"]
        for t, c in sorted(types_count.items()):
            caption_lines.append(f"  {t}: {c}")
        caption_lines.append(
            "\n_Import this file in Ghostfolio → Portfolio → Activities → Import._"
        )

        await query.edit_message_text("Generating JSON file…")
        await query.message.reply_document(
            document=buf,
            filename=buf.name,
            caption="\n".join(caption_lines),
            parse_mode="Markdown",
        )
    except Exception:
        logger.exception("Unexpected error generating import JSON")
        await query.edit_message_text("Unexpected error generating JSON file.")


async def _do_direct_import(query, to_import: list[dict], parser_name: str) -> None:
    """POST activities directly to Ghostfolio."""
    try:
        async with GhostfolioClient(
            settings.ghostfolio_url, settings.ghostfolio_access_token
        ) as gf:
            created, skipped_symbols = await gf.import_activities(to_import)

        types_count: dict[str, int] = {}
        for a in to_import:
            types_count[a["type"]] = types_count.get(a["type"], 0) + 1

        summary_lines = [f"Imported *{created}* activities from *{parser_name}*:\n"]
        for t, c in sorted(types_count.items()):
            summary_lines.append(f"  {t}: {c}")

        if skipped_symbols:
            unique = sorted(set(skipped_symbols))
            summary_lines.append(
                f"\n⚠️ *{len(skipped_symbols)} activities skipped* — symbol not found on data source:\n"
                + ", ".join(f"`{s}`" for s in unique)
            )
        elif created < len(to_import):
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


async def import_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the import flow via /cancel command."""
    if update.message:
        await update.message.reply_text("Import cancelled.", reply_markup=ReplyKeyboardRemove())
    _clear_import_state(context)
    return ConversationHandler.END


def _broker_keyboard() -> list[list[str]]:
    """Build a reply keyboard with all registered broker names."""
    parsers = get_all_parsers()
    names = [cls().name for cls in parsers.values()]
    return [names[i : i + 2] for i in range(0, len(names), 2)]


# Yahoo Finance symbol suffixes and the native currency they imply.
# Brokers like DEGIRO convert these transactions to the account currency (EUR),
# creating a mismatch that causes Ghostfolio to crash during portfolio calculation.
_SUFFIX_NATIVE_CURRENCY: dict[str, set[str]] = {
    ".L": {"GBp", "GBX", "GBP"},   # London Stock Exchange → GBp
    ".IL": {"ILA", "ILS"},          # Tel Aviv Stock Exchange → ILS
}


def _filter_currency_mismatches(activities: list[dict]) -> tuple[list[dict], list[dict]]:
    """Remove activities where the symbol's implied native currency doesn't match
    the recorded currency. Returns (filtered_list, skipped_activities)."""
    ok: list[dict] = []
    skipped: list[dict] = []
    for a in activities:
        symbol = a.get("symbol", "")
        currency = a.get("currency", "")
        mismatch = False
        for suffix, native_currencies in _SUFFIX_NATIVE_CURRENCY.items():
            if symbol.endswith(suffix) and currency not in native_currencies:
                logger.warning(
                    "Skipping %s (currency mismatch: symbol implies %s but activity has %s)",
                    symbol, next(iter(native_currencies)), currency,
                )
                skipped.append(a)
                mismatch = True
                break
        if not mismatch:
            ok.append(a)
    return ok, skipped


def _split_message(text: str, max_len: int = 4096) -> list[str]:
    """Split a long message into chunks ≤ max_len, breaking on newlines."""
    chunks: list[str] = []
    while len(text) > max_len:
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


def _clear_import_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (
        "import_broker", "csv_content", "file_bytes", "is_xls",
        "pending_activities", "pending_import", "pending_parser_name",
    ):
        context.user_data.pop(key, None)


def build_import_conversation() -> ConversationHandler:
    """Build and return the ConversationHandler for /import."""
    return ConversationHandler(
        entry_points=[
            CommandHandler("import", import_start),
            MessageHandler(filters.Document.FileExtension("csv"), import_receive_file),
        ],
        states={
            UPLOAD_FILE: [
                MessageHandler(filters.Document.ALL, import_receive_file),
            ],
            SELECT_BROKER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, import_select_broker),
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
        allow_reentry=True,
    )
