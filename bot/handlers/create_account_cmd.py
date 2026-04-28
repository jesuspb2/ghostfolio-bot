"""Handler for /create_account — guided flow to create a Ghostfolio account.

Flow: /create_account → name → currency → balance → confirm
"""

from __future__ import annotations

import logging
from typing import Any

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.config import settings
from bot.ghostfolio_client import GhostfolioClient, GhostfolioError
from bot.utils.auth import restricted

logger = logging.getLogger(__name__)

NAME, CURRENCY, BALANCE, CONFIRM = range(4)


@restricted
async def create_account_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text(
            "Account name? (e.g. Interactive Brokers, Revolut)",
            reply_markup=ReplyKeyboardRemove(),
        )
    return NAME


async def create_account_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return ConversationHandler.END
    assert context.user_data is not None

    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Name cannot be empty. Try again.")
        return NAME

    context.user_data["account_name"] = name
    keyboard = [["EUR", "USD"], ["GBP", "CHF"]]
    await update.message.reply_text(
        "Currency?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True),
    )
    return CURRENCY


async def create_account_currency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return ConversationHandler.END
    assert context.user_data is not None

    context.user_data["account_currency"] = update.message.text.upper().strip()
    await update.message.reply_text(
        "Initial balance? (e.g. 1000, or 0)",
        reply_markup=ReplyKeyboardRemove(),
    )
    return BALANCE


async def create_account_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return ConversationHandler.END
    assert context.user_data is not None

    try:
        balance = float(update.message.text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("Invalid number. Try again (e.g. 0, 1000.50).")
        return BALANCE

    context.user_data["account_balance"] = balance
    d: dict[str, Any] = context.user_data
    await update.message.reply_text(
        f"*Create account?*\n\n"
        f"Name: {d['account_name']}\n"
        f"Currency: {d['account_currency']}\n"
        f"Balance: {d['account_balance']}\n\n"
        "Send /confirm to create or /cancel to abort.",
        parse_mode="Markdown",
    )
    return CONFIRM


async def create_account_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message:
        return ConversationHandler.END
    assert context.user_data is not None

    d: dict[str, Any] = context.user_data
    try:
        async with GhostfolioClient(
            settings.ghostfolio_url, settings.ghostfolio_access_token
        ) as gf:
            result = await gf.create_account(
                name=d["account_name"],
                currency=d["account_currency"],
                balance=d["account_balance"],
            )

        account_id = result.get("id") or result.get("accountId") or "?"
        await update.message.reply_text(
            f"Account created: *{d['account_name']}* ({d['account_currency']})\nID: `{account_id}`",
            parse_mode="Markdown",
        )
    except GhostfolioError as e:
        logger.error("Failed to create account: %s", e)
        await update.message.reply_text(f"Failed to create account: {e.detail}")
    except Exception:
        logger.exception("Unexpected error in /create_account confirm")
        await update.message.reply_text("Unexpected error creating account.")

    context.user_data.clear()
    return ConversationHandler.END


async def create_account_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text("Cancelled.", reply_markup=ReplyKeyboardRemove())
    if context.user_data:
        context.user_data.clear()
    return ConversationHandler.END


def build_create_account_conversation() -> ConversationHandler:  # type: ignore[type-arg]
    return ConversationHandler(
        entry_points=[CommandHandler("create_account", create_account_start)],
        states={
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_account_name)],
            CURRENCY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, create_account_currency)
            ],
            BALANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_account_balance)],
            CONFIRM: [
                CommandHandler("confirm", create_account_confirm),
                CommandHandler("cancel", create_account_cancel),
            ],
        },
        fallbacks=[CommandHandler("cancel", create_account_cancel)],
    )
