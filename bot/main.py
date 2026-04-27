"""Ghostfolio Companion Bot — entry point."""

from __future__ import annotations

import logging

from telegram.ext import ApplicationBuilder, CommandHandler

from bot.config import settings
from bot.handlers.import_cmd import build_import_conversation
from bot.handlers.portfolio_cmd import portfolio_handler
from bot.handlers.start_cmd import help_handler, start_handler


def main() -> None:
    """Initialize and run the bot."""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    logger.info("Starting Ghostfolio Companion Bot")
    logger.info("Ghostfolio URL: %s", settings.ghostfolio_url)

    app = ApplicationBuilder().token(settings.telegram_bot_token).build()

    app.add_handler(build_import_conversation())

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("portfolio", portfolio_handler))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
