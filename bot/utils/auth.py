"""Telegram bot access control."""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import settings

logger = logging.getLogger(__name__)


def restricted(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that restricts handler to TELEGRAM_ALLOWED_USERS.

    If the list is empty, all users are allowed (useful for development).
    """

    @functools.wraps(func)
    async def wrapper(
        update: Update, context: ContextTypes.DEFAULT_TYPE, *args: Any, **kwargs: Any
    ) -> Any:
        user = update.effective_user
        if not user:
            return None

        if settings.telegram_allowed_users and user.id not in settings.telegram_allowed_users:
            logger.warning("Unauthorized access attempt by user %s (%s)", user.id, user.username)
            if update.message:
                await update.message.reply_text("⛔ You are not authorized to use this bot.")
            return None

        return await func(update, context, *args, **kwargs)

    return wrapper
