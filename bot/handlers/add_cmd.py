"""Handler for /add — guided conversational flow to add a manual transaction.

Flow: /add → type (BUY/SELL/DIVIDEND) → symbol → quantity → price → fee → currency → date → confirm
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
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
from bot.utils.formatters import format_activity_confirm

logger = logging.getLogger(__name__)

TYPE, SYMBOL, DATA_SOURCE, QUANTITY, PRICE, FEE, CURRENCY, DATE, CONFIRM = range(9)


@restricted
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: ask for transaction type."""
    keyboard = [["BUY", "SELL"], ["DIVIDEND"]]
    if update.message:
        await update.message.reply_text(
            "What type of transaction?",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True),
        )
    return TYPE


async def add_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store type, ask for symbol."""
    if not update.message or not update.message.text:
        return ConversationHandler.END
    assert context.user_data is not None

    text = update.message.text.upper().strip()
    if text not in ("BUY", "SELL", "DIVIDEND"):
        await update.message.reply_text("Please choose BUY, SELL, or DIVIDEND.")
        return TYPE

    context.user_data["type"] = text
    await update.message.reply_text(
        "Ticker symbol? (e.g. MSFT, VWCE.DE, BTC-USD)",
        reply_markup=ReplyKeyboardRemove(),
    )
    return SYMBOL


async def add_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store symbol, ask for data source."""
    if not update.message or not update.message.text:
        return ConversationHandler.END
    assert context.user_data is not None

    context.user_data["symbol"] = update.message.text.upper().strip()
    keyboard = [["YAHOO", "COINGECKO"], ["MANUAL", "OTHER"]]
    await update.message.reply_text(
        "Data source?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True),
    )
    return DATA_SOURCE


async def add_data_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store data source, ask for quantity."""
    if not update.message or not update.message.text:
        return ConversationHandler.END
    assert context.user_data is not None

    context.user_data["data_source"] = update.message.text.upper().strip()
    await update.message.reply_text("Quantity? (e.g. 10, 0.5)", reply_markup=ReplyKeyboardRemove())
    return QUANTITY


async def add_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store quantity, ask for unit price."""
    if not update.message or not update.message.text:
        return ConversationHandler.END
    assert context.user_data is not None

    try:
        context.user_data["quantity"] = float(update.message.text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("Invalid number. Try again (e.g. 10, 0.5).")
        return QUANTITY

    await update.message.reply_text("Unit price? (e.g. 298.58)")
    return PRICE


async def add_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store price, ask for fee."""
    if not update.message or not update.message.text:
        return ConversationHandler.END
    assert context.user_data is not None

    try:
        context.user_data["unit_price"] = float(update.message.text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("Invalid number. Try again.")
        return PRICE

    await update.message.reply_text("Fee? (e.g. 1.50, or 0 if none)")
    return FEE


async def add_fee(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store fee, ask for currency."""
    if not update.message or not update.message.text:
        return ConversationHandler.END
    assert context.user_data is not None

    try:
        context.user_data["fee"] = float(update.message.text.replace(",", "."))
    except ValueError:
        await update.message.reply_text("Invalid number. Try again.")
        return FEE

    keyboard = [["EUR", "USD"], ["GBP", "CHF"]]
    await update.message.reply_text(
        "Currency?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True),
    )
    return CURRENCY


async def add_currency(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store currency, ask for date."""
    if not update.message or not update.message.text:
        return ConversationHandler.END
    assert context.user_data is not None

    context.user_data["currency"] = update.message.text.upper().strip()
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    await update.message.reply_text(
        f"Date? (YYYY-MM-DD format, or 'today' for {today})",
        reply_markup=ReplyKeyboardRemove(),
    )
    return DATE


async def add_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store date, show confirmation."""
    if not update.message or not update.message.text:
        return ConversationHandler.END
    assert context.user_data is not None

    text = update.message.text.strip().lower()
    if text == "today":
        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
    else:
        try:
            datetime.strptime(text, "%Y-%m-%d")
            date_str = text
        except ValueError:
            await update.message.reply_text("Invalid date format. Use YYYY-MM-DD or 'today'.")
            return DATE

    context.user_data["date"] = date_str
    d: dict[str, Any] = context.user_data

    msg = format_activity_confirm(
        symbol=d["symbol"],
        type_=d["type"],
        quantity=d["quantity"],
        unit_price=d["unit_price"],
        fee=d["fee"],
        currency=d["currency"],
        date=d["date"],
        data_source=d["data_source"],
    )
    await update.message.reply_text(msg, parse_mode="Markdown")
    return CONFIRM


async def add_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Import the activity into Ghostfolio."""
    if not update.message:
        return ConversationHandler.END
    assert context.user_data is not None

    d: dict[str, Any] = context.user_data
    try:
        async with GhostfolioClient(
            settings.ghostfolio_url, settings.ghostfolio_access_token
        ) as gf:
            await gf.add_activity(
                symbol=d["symbol"],
                type_=d["type"],
                quantity=d["quantity"],
                unit_price=d["unit_price"],
                fee=d["fee"],
                currency=d["currency"],
                date=datetime.strptime(d["date"], "%Y-%m-%d").replace(tzinfo=UTC),
                account_id=settings.ghostfolio_account_id,
                data_source=d["data_source"],
            )

        await update.message.reply_text(
            f"Transaction imported: {d['type']} {d['quantity']} x {d['symbol']}"
        )
    except GhostfolioError as e:
        logger.error("Import failed: %s", e)
        await update.message.reply_text(f"Import failed: {e.detail}")
    except Exception:
        logger.exception("Unexpected error in /add confirm")
        await update.message.reply_text("Unexpected error importing transaction.")

    context.user_data.clear()
    return ConversationHandler.END


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the /add flow."""
    if update.message:
        await update.message.reply_text(
            "Transaction cancelled.", reply_markup=ReplyKeyboardRemove()
        )
    if context.user_data:
        context.user_data.clear()
    return ConversationHandler.END


def build_add_conversation() -> ConversationHandler:  # type: ignore[type-arg]
    """Build and return the ConversationHandler for /add."""
    return ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_type)],
            SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_symbol)],
            DATA_SOURCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_data_source)],
            QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_quantity)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_price)],
            FEE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_fee)],
            CURRENCY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_currency)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_date)],
            CONFIRM: [
                CommandHandler("confirm", add_confirm),
                CommandHandler("cancel", add_cancel),
            ],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )
