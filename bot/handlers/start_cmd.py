"""Handler for /start command and unknown message fallback."""

from __future__ import annotations

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.utils.auth import restricted

WELCOME_TEXT = r"""
👋 *Welcome to your Ghostfolio Companion Bot\!*

Here's what I can do for you:

📊 /portfolio — Current portfolio value, P&L, and top holdings
📈 /performance — Gains and losses by period \(today, week, month, YTD, 1y, all time\)
💰 /dividends — Dividend history and monthly chart
📥 /import — Import transactions from a broker CSV \(DEGIRO, IBKR, Revolut…\)
🔄 /sync\_ibkr — Sync IBKR trades directly into Ghostfolio
💾 /backup — Export your Ghostfolio data to a local file or Telegram
♻️ /restore — Restore Ghostfolio data from a backup
🏦 /create\_account — Create a new account in Ghostfolio

Type any command to get started\!
"""


async def _send_welcome(update: Update) -> None:
    if update.message:
        await update.message.chat.send_action(ChatAction.TYPING)
        await update.message.reply_text(WELCOME_TEXT, parse_mode="MarkdownV2")


@restricted
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send welcome message."""
    await _send_welcome(update)


@restricted
async def fallback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply to unknown messages with the command list."""
    await _send_welcome(update)
