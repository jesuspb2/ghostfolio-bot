"""Ghostfolio Companion Bot — entry point."""

from __future__ import annotations

import logging
from typing import Any

from telegram import BotCommand
from telegram.ext import Application, ApplicationBuilder, CommandHandler, MessageHandler, filters

from bot.backup.scheduler import start_scheduler, stop_scheduler
from bot.config import settings
from bot.handlers.backup_cmd import backup_handler, build_restore_conversation
from bot.handlers.create_account_cmd import build_create_account_conversation
from bot.handlers.dividends_cmd import dividends_handler
from bot.handlers.import_cmd import build_import_conversation
from bot.handlers.performance_cmd import performance_handler
from bot.handlers.portfolio_cmd import portfolio_handler
from bot.handlers.start_cmd import fallback_handler, start_handler
from bot.handlers.sync_ibkr_cmd import build_sync_ibkr_conversation

_COMMANDS = [
    BotCommand("start", "Welcome message and command list"),
    BotCommand("portfolio", "Portfolio summary (value, P&L, top holdings)"),
    BotCommand("performance", "Performance by period: today, week, month, YTD, 1y, all time"),
    BotCommand("dividends", "Dividend history: this month, year, and monthly chart"),
    BotCommand("import", "Import transactions from a broker CSV"),
    BotCommand("sync_ibkr", "Sync IBKR trades directly to Ghostfolio"),
    BotCommand("backup", "Export Ghostfolio data to local file or Telegram"),
    BotCommand("restore", "Restore Ghostfolio data from a backup"),
    BotCommand("create_account", "Create a new Ghostfolio account"),
]


async def _post_init(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    await app.bot.set_my_commands(_COMMANDS)
    start_scheduler(app, settings)


async def _post_stop(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    stop_scheduler(app)


def main() -> None:
    """Initialize and run the bot."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # httpx logs full request URLs at INFO. IBKR Flex credentials are query
    # parameters, so keep transport logs at WARNING to avoid leaking tokens.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # python-telegram-bot includes the bot token in transport URLs at DEBUG.
    logging.getLogger("telegram").setLevel(logging.INFO)
    logger = logging.getLogger(__name__)

    logger.info("Starting Ghostfolio Companion Bot")
    logger.info("Ghostfolio URL: %s", settings.ghostfolio_url)

    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .post_stop(_post_stop)
        .build()
    )

    app.add_handler(build_import_conversation())
    app.add_handler(build_sync_ibkr_conversation())
    app.add_handler(build_create_account_conversation())
    app.add_handler(build_restore_conversation())

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("portfolio", portfolio_handler))
    app.add_handler(CommandHandler("performance", performance_handler))
    app.add_handler(CommandHandler("dividends", dividends_handler))
    app.add_handler(CommandHandler("backup", backup_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, fallback_handler))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
