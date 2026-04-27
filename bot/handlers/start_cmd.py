"""Handler for /start and /help commands."""

from __future__ import annotations

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.utils.auth import restricted

HELP_TEXT = """
*Ghostfolio Companion Bot*

Available commands:

/portfolio — Portfolio summary (value, P&L, top holdings)
/import — Import transactions from a broker CSV
/help — Show this message
"""


@restricted
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send welcome message."""
    if update.message:
        await update.message.chat.send_action(ChatAction.TYPING)
        await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


@restricted
async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send help message."""
    if update.message:
        await update.message.chat.send_action(ChatAction.TYPING)
        await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
