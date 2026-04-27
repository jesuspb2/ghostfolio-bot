"""Handler for /portfolio command."""

from __future__ import annotations

import logging
from datetime import datetime

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.config import settings
from bot.ghostfolio_client import GhostfolioClient, GhostfolioError
from bot.utils.auth import restricted

logger = logging.getLogger(__name__)


def _fmt_eur(value: float, currency: str = "EUR") -> str:
    symbols = {"EUR": "€", "USD": "$", "GBP": "£"}
    sym = symbols.get(currency, f"{currency} ")
    return f"{sym}{abs(value):,.2f}"


def _fmt_signed(value: float, currency: str = "EUR") -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}{_fmt_eur(value, currency)}"


def _fmt_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.1f}%"


def _years_since(date_str: str) -> str:
    try:
        start = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
        from datetime import date
        years = (date.today() - start).days / 365.25
        return f"{years:.1f}y"
    except Exception:
        return "?"


@restricted
async def portfolio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch and display portfolio summary."""
    if not update.message:
        return

    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        async with GhostfolioClient(
            settings.ghostfolio_url, settings.ghostfolio_access_token
        ) as gf:
            details = await gf.portfolio_details()
            perf_ytd = await gf.portfolio_performance(range_="ytd")

    except GhostfolioError as e:
        logger.error("Ghostfolio API error: %s", e)
        await update.message.reply_text(f"Error connecting to Ghostfolio: {e.detail}")
        return
    except Exception:
        logger.exception("Unexpected error in /portfolio")
        await update.message.reply_text("Unexpected error fetching portfolio data.")
        return

    summary = details.get("summary", {})
    holdings = details.get("holdings", {})
    currency = summary.get("currency", "EUR")

    total = summary.get("currentValueInBaseCurrency", 0)
    total_gain = summary.get("netPerformance", 0)
    total_gain_pct = summary.get("netPerformancePercentage", 0)
    annualized = summary.get("annualizedPerformancePercent")
    first_date = summary.get("dateOfFirstActivity", "")

    ytd_perf = perf_ytd.get("performance", {})
    ytd_gain = ytd_perf.get("netPerformance", 0)
    ytd_gain_pct = ytd_perf.get("netPerformancePercentage", 0)

    lines = [f"💼 <b>Total: {_fmt_eur(total, currency)}</b>", ""]

    # Top holdings sorted by value
    if holdings:
        sorted_h = sorted(
            holdings.items(),
            key=lambda kv: kv[1].get("valueInBaseCurrency", 0),
            reverse=True,
        )
        lines.append("📊 <b>Holdings:</b>")
        for key, h in sorted_h[:10]:
            name = h.get("name") or key
            value = h.get("valueInBaseCurrency", 0)
            alloc = h.get("allocationInPercentage", 0) * 100
            gain = h.get("netPerformance", 0)
            gain_pct = h.get("netPerformancePercent", 0)
            lines.append(
                f"   <b>{name}</b> ({alloc:.0f}%) — {_fmt_eur(value, currency)}"
                f"  {_fmt_signed(gain, currency)} ({_fmt_pct(gain_pct)})"
            )
        lines.append("")

    lines.append("📈 <b>Performance:</b>")
    lines.append(f"   Total: {_fmt_signed(total_gain, currency)} ({_fmt_pct(total_gain_pct)})")
    lines.append(f"   YTD:   {_fmt_signed(ytd_gain, currency)} ({_fmt_pct(ytd_gain_pct)})")
    if annualized is not None:
        lines.append(f"   Annualized: {_fmt_pct(annualized)}")
    if first_date:
        lines.append(f"   Since {first_date[:10]} ({_years_since(first_date)})")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
