"""Telegram flow for syncing an IBKR Flex Query directly to Ghostfolio."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import date, timedelta
from typing import Any

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
)

from bot.config import settings
from bot.ghostfolio_client import GhostfolioClient, GhostfolioError, deduplicate_activities
from bot.ibkr_flex_client import IbkrFlexClient, IbkrFlexError
from bot.ibkr_sync import IbkrSyncError, prepare_ibkr_sync, select_ghostfolio_account
from bot.utils.auth import restricted

logger = logging.getLogger(__name__)

SYNC_CONFIRM = 0
_CONFIRM_CB = "ibkr_sync_confirm"
_CANCEL_CB = "ibkr_sync_cancel"
_STATE_KEY = "pending_ibkr_sync"


@asynccontextmanager
async def _typing(chat: Any) -> AsyncIterator[None]:
    """Keep the Telegram typing indicator active during the remote sync preview."""
    async def _loop() -> None:
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
        with suppress(asyncio.CancelledError):
            await task


@restricted
async def sync_ibkr_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Fetch a Flex report and show a deduplicated import preview."""
    if not update.message:
        return ConversationHandler.END
    assert context.user_data is not None
    _clear_sync_state(context)

    token_setting = settings.ibkr_flex_token
    token = token_setting.get_secret_value().strip() if token_setting else ""
    query_id = (settings.ibkr_flex_query_id or "").strip()
    confirmation_query_id = (
        settings.ibkr_trade_confirmation_query_id or ""
    ).strip()
    configured_account_id = (
        settings.ibkr_ghostfolio_account_id.strip()
        if settings.ibkr_ghostfolio_account_id
        else None
    )

    missing: list[str] = []
    if not token:
        missing.append("IBKR_FLEX_TOKEN")
    if not query_id:
        missing.append("IBKR_FLEX_QUERY_ID")
    if missing:
        await update.message.reply_text(
            "IBKR sync is not configured. Missing: " + ", ".join(missing)
        )
        return ConversationHandler.END

    status_message = await update.message.reply_text(
        "🔄 Generating the latest completed IBKR report…"
    )

    try:
        async with _typing(update.message.chat):
            flex = IbkrFlexClient(token=token, query_id=query_id)
            today = date.today()
            for lag_days in range(1, 4):
                to_date = today - timedelta(days=lag_days)
                from_date = to_date - timedelta(
                    days=settings.ibkr_flex_lookback_days - 1
                )
                try:
                    csv_content = await flex.fetch_csv(
                        from_date=from_date,
                        to_date=to_date,
                    )
                    break
                except IbkrFlexError as exc:
                    if exc.code != "1003" or lag_days == 3:
                        raise
                    # IBKR Activity statements are produced once daily. Around
                    # the processing window, yesterday may not be available yet.
                    await asyncio.sleep(1.1)

            trade_confirmation_content: str | None = None
            if confirmation_query_id:
                # IBKR limits Flex SendRequest calls to roughly one per second.
                await asyncio.sleep(1.1)
                confirmation_flex = IbkrFlexClient(
                    token=token,
                    query_id=confirmation_query_id,
                )
                try:
                    trade_confirmation_content = await confirmation_flex.fetch_csv()
                except IbkrFlexError as exc:
                    if exc.code != "1003":
                        raise
                    # A current-day Trade Confirmation can legitimately be empty.
                    logger.info("No IBKR Trade Confirmations are available for today")

            async with GhostfolioClient(
                settings.ghostfolio_url,
                settings.ghostfolio_access_token,
            ) as ghostfolio:
                accounts = await ghostfolio.get_accounts()
                account_id, account_name = select_ghostfolio_account(
                    accounts,
                    account_id=configured_account_id,
                    account_name=settings.ibkr_ghostfolio_account_name,
                )
                preview = await prepare_ibkr_sync(
                    csv_content,
                    account_id,
                    ghostfolio,
                    trade_confirmation_content=trade_confirmation_content,
                )

        if not preview.to_import:
            extra = _skipped_summary(
                preview.unresolved_activities,
                preview.invalid_currency_activities,
            )
            await status_message.edit_text(
                "✅ IBKR and Ghostfolio are already in sync.\n\n"
                f"Ghostfolio account: {account_name}\n"
                f"Historical trades: {preview.historical_activities}\n"
                f"Today's executions: {preview.intraday_activities}\n"
                f"Duplicates already present: {preview.duplicates_skipped}"
                f"{extra}"
            )
            return ConversationHandler.END

        context.user_data[_STATE_KEY] = preview.to_import
        lines = [
            "🔄 IBKR → Ghostfolio",
            "",
            f"Period: {from_date.isoformat()} → {to_date.isoformat()}",
            f"Ghostfolio account: {account_name}",
            f"Historical trades: {preview.historical_activities}",
            f"Today's executions: {preview.intraday_activities}",
            f"Already in Ghostfolio: {preview.duplicates_skipped}",
            f"New trades: {len(preview.to_import)}",
        ]
        if preview.unresolved_activities:
            lines.append(f"⚠️ Unresolved symbols skipped: {preview.unresolved_activities}")
        if preview.invalid_currency_activities:
            lines.append(
                f"⚠️ Invalid currencies skipped: {preview.invalid_currency_activities}"
            )
        if preview.validation_errors:
            lines.extend(["", "⚠️ Ghostfolio validation warnings:"])
            lines.extend(f"• {error[:180]}" for error in preview.validation_errors[:3])

        lines.extend(["", "New trades:"])
        for activity in preview.to_import[:8]:
            lines.append(
                f"• {str(activity.get('date', ''))[:10]} {activity.get('type', '')} "
                f"{activity.get('quantity', 0)} x {activity.get('symbol', '?')} @ "
                f"{activity.get('unitPrice', 0)} {activity.get('currency', '')}"
            )
        if len(preview.to_import) > 8:
            lines.append(f"• …and {len(preview.to_import) - 8} more")

        lines.extend(["", "Import these trades directly into Ghostfolio?"])
        keyboard = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    f"✅ Sync {len(preview.to_import)} trades",
                    callback_data=_CONFIRM_CB,
                ),
                InlineKeyboardButton("❌ Cancel", callback_data=_CANCEL_CB),
            ]]
        )
        await status_message.edit_text("\n".join(lines), reply_markup=keyboard)
        return SYNC_CONFIRM

    except IbkrFlexError as exc:
        logger.warning("IBKR Flex sync failed (code=%s): %s", exc.code, exc.detail)
        await status_message.edit_text(_friendly_flex_error(exc))
    except IbkrSyncError as exc:
        logger.warning("IBKR report format error: %s", exc)
        await status_message.edit_text(f"IBKR report error: {exc}")
    except GhostfolioError as exc:
        logger.error("Ghostfolio error during IBKR sync preview: %s", exc)
        await status_message.edit_text(f"Ghostfolio error: {exc.detail}")
    except Exception:
        logger.exception("Unexpected error during IBKR sync preview")
        await status_message.edit_text("Unexpected error while preparing the IBKR sync.")

    _clear_sync_state(context)
    return ConversationHandler.END


async def sync_ibkr_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Import the prepared, deduplicated activities after explicit confirmation."""
    if not update.callback_query:
        return ConversationHandler.END
    assert context.user_data is not None
    query: CallbackQuery = update.callback_query
    await query.answer()

    if query.data == _CANCEL_CB:
        _clear_sync_state(context)
        await query.edit_message_text("IBKR sync cancelled.")
        return ConversationHandler.END

    activities = context.user_data.pop(_STATE_KEY, None)
    if not activities:
        await query.edit_message_text("The sync preview expired. Run /sync_ibkr again.")
        return ConversationHandler.END

    await query.edit_message_text("🔄 Importing IBKR trades into Ghostfolio…")
    try:
        assert query.message is not None
        async with (
            _typing(query.message.chat),
            GhostfolioClient(
                settings.ghostfolio_url,
                settings.ghostfolio_access_token,
            ) as ghostfolio,
        ):
            # Re-check immediately before writing, so a second sync or another
            # importer cannot create duplicates after the preview was prepared.
            account_id = str(activities[0].get("accountId", ""))
            existing = await ghostfolio.get_orders(account_id=account_id)
            existing_list = (
                existing.get("activities", [])
                if isinstance(existing, dict)
                else existing or []
            )
            activities = deduplicate_activities(activities, existing_list)
            if not activities:
                await query.edit_message_text(
                    "✅ IBKR and Ghostfolio are already in sync. Nothing to import."
                )
                return ConversationHandler.END
            created, skipped_symbols = await ghostfolio.import_activities(activities)

        lines = [f"✅ IBKR sync complete: {created} trades imported."]
        if skipped_symbols:
            unique_symbols = ", ".join(sorted(set(skipped_symbols)))
            lines.append(
                f"⚠️ {len(skipped_symbols)} trades were skipped because Ghostfolio "
                f"could not resolve: {unique_symbols}"
            )
        elif created < len(activities):
            lines.append(f"⚠️ {len(activities) - created} trades were not imported.")
        await query.edit_message_text("\n".join(lines))
    except GhostfolioError as exc:
        logger.error("Ghostfolio error during IBKR sync import: %s", exc)
        await query.edit_message_text(f"Ghostfolio import failed: {exc.detail}")
    except Exception:
        logger.exception("Unexpected error importing the IBKR sync")
        await query.edit_message_text("Unexpected error while importing IBKR trades.")
    finally:
        _clear_sync_state(context)

    return ConversationHandler.END


async def sync_ibkr_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel an IBKR sync using /cancel."""
    _clear_sync_state(context)
    if update.message:
        await update.message.reply_text("IBKR sync cancelled.")
    return ConversationHandler.END


def build_sync_ibkr_conversation() -> ConversationHandler:  # type: ignore[type-arg]
    """Build the /sync_ibkr preview and confirmation flow."""
    return ConversationHandler(
        entry_points=[CommandHandler("sync_ibkr", sync_ibkr_start)],
        states={
            SYNC_CONFIRM: [
                CallbackQueryHandler(
                    sync_ibkr_confirm,
                    pattern=f"^({_CONFIRM_CB}|{_CANCEL_CB})$",
                )
            ]
        },
        fallbacks=[CommandHandler("cancel", sync_ibkr_cancel)],
        allow_reentry=True,
    )


def _clear_sync_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data is not None:
        context.user_data.pop(_STATE_KEY, None)


def _friendly_flex_error(error: IbkrFlexError) -> str:
    messages = {
        "1003": "The latest IBKR Activity Statement is not available yet. Try again later.",
        "1012": "The IBKR Flex token has expired. Generate a new token in Client Portal.",
        "1013": "IBKR rejected this server's IP address. Check the token IP restriction.",
        "1014": "The IBKR Flex Query ID is invalid or is not visible to this account.",
        "1015": "The IBKR Flex token is invalid.",
        "1018": "IBKR's Flex request limit was reached. Try again in one minute.",
        "1019": "IBKR did not finish generating the report in time. Try again shortly.",
    }
    detail = messages.get(error.code, error.detail) if error.code else error.detail
    return "IBKR sync failed: " + detail


def _skipped_summary(unresolved: int, invalid_currency: int) -> str:
    lines: list[str] = []
    if unresolved:
        lines.append(f"\nUnresolved symbols skipped: {unresolved}")
    if invalid_currency:
        lines.append(f"\nInvalid currencies skipped: {invalid_currency}")
    return "".join(lines)
