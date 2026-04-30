"""Handler for /performance command."""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.config import settings
from bot.ghostfolio_client import GhostfolioClient, GhostfolioError
from bot.utils.auth import restricted

logger = logging.getLogger(__name__)

_RANGES: list[tuple[str, str]] = [
    ("1d", "Today"),
    ("wtd", "This week"),
    ("mtd", "This month"),
    ("ytd", "YTD"),
    ("1y", "1 year"),
    ("max", "All time"),
]


def _fmt_signed(value: float, currency: str) -> str:
    symbols = {"EUR": "€", "USD": "$", "GBP": "£"}
    sym = symbols.get(currency, f"{currency} ")
    sign = "+" if value >= 0 else "-"
    return f"{sign}{sym}{abs(value):,.2f}"


def _fmt_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.2f}%"


@restricted
async def performance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch and display performance across multiple time ranges."""
    if not update.message:
        return

    await update.message.reply_text("⏳ Fetching performance data, hang tight...")
    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        async with GhostfolioClient(
            settings.ghostfolio_url, settings.ghostfolio_access_token
        ) as gf:
            perf_results, details = await asyncio.gather(
                asyncio.gather(
                    *[gf.portfolio_performance(range_=r) for r, _ in _RANGES],
                    return_exceptions=True,
                ),
                gf.portfolio_details(),
                return_exceptions=False,
            )
    except GhostfolioError as e:
        logger.error("Ghostfolio API error: %s", e)
        await update.message.reply_text(f"Error connecting to Ghostfolio: {e.detail}")
        return
    except Exception:
        logger.exception("Unexpected error in /performance")
        await update.message.reply_text("Unexpected error fetching performance data.")
        return

    results = perf_results
    summary = details.get("summary", {})
    currency = summary.get("currency", "EUR")

    # Build table rows as plain text (wrapped in one <code> block at the end)
    col1, col2, col3 = 13, 16, 10
    sep = "─" * (col1 + col2 + col3 + 2)
    rows = [
        f"{'Period':<{col1}} {'Gain/Loss':>{col2}}  {'%':>{col3}}",
        sep,
    ]

    for (range_key, label), res in zip(_RANGES, results, strict=True):
        if isinstance(res, BaseException):
            logger.warning("Failed to fetch range %s: %s", range_key, res)
            rows.append(f"{label:<{col1}} {'N/A':>{col2}}  {'N/A':>{col3}}")
            continue

        perf = res.get("performance", {})
        gain = perf.get("netPerformance", 0.0)
        pct = perf.get("netPerformancePercentage", 0.0)
        gain_str = _fmt_signed(gain, currency)
        rows.append(f"{label:<{col1}} {gain_str:>{col2}}  {_fmt_pct(pct):>{col3}}")

    # Annualized from portfolio details summary
    annualized_line = ""
    ann = summary.get("annualizedPerformancePercent")
    if ann is not None:
        annualized_line = f"\n{sep}\n{'Annualized:':<{col1}} {'':>{col2}}  {_fmt_pct(ann):>{col3}}"

    table = "\n".join(rows)
    text = f"📈 <b>Performance</b>\n\n<code>{table}{annualized_line}</code>"

    await update.message.reply_text(text, parse_mode="HTML")
