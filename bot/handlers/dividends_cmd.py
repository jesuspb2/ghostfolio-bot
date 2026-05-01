"""Handler for /dividends command."""

from __future__ import annotations

import io
import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import matplotlib
import matplotlib.pyplot as plt
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.config import settings
from bot.ghostfolio_client import GhostfolioClient, GhostfolioError
from bot.utils.auth import restricted

matplotlib.use("Agg")

logger = logging.getLogger(__name__)

_BG = "#1c1c1e"
_FG = "#ebebf0"
_GREEN = "#30d158"
_GRID = "#3a3a3c"


def _symbol_name(activity: dict[str, Any]) -> str:
    sp = activity.get("SymbolProfile") or {}
    return str(sp.get("name") or sp.get("symbol") or activity.get("symbol", "?"))


def _amount(activity: dict[str, Any]) -> float:
    return float(activity.get("quantity", 1.0)) * float(activity.get("unitPrice", 0.0))


def _parse_date(date_str: str) -> datetime:
    return datetime.fromisoformat(date_str.replace("Z", "+00:00"))


def _fmt(value: float, currency: str) -> str:
    sym = {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency, f"{currency} ")
    return f"{sym}{value:,.2f}"


def _build_chart(monthly: dict[str, float], currency: str) -> io.BytesIO:
    """Bar chart of monthly dividends for the last 12 months."""
    # Fill zero for any missing month in the last 12
    now = datetime.now(UTC)
    all_months = [
        datetime(now.year if now.month - i > 0 else now.year - 1,
                 (now.month - i - 1) % 12 + 1, 1)
        for i in range(11, -1, -1)
    ]
    labels = [dt.strftime("%b %y") for dt in all_months]
    keys = [dt.strftime("%Y-%m") for dt in all_months]
    values = [monthly.get(k, 0.0) for k in keys]

    sym = {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency, f"{currency} ")
    max_val = max(values) if any(v > 0 for v in values) else 1

    fig, ax = plt.subplots(figsize=(11, 5))
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)

    bars = ax.bar(labels, values, color=_GREEN, edgecolor=_BG, linewidth=0.5)

    for bar, val in zip(bars, values, strict=True):
        if val > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max_val * 0.015,
                f"{sym}{val:,.0f}",
                ha="center", va="bottom", fontsize=7.5, color=_FG,
            )

    ax.set_title(
        "Monthly Dividends — last 12 months",
        color=_FG, fontsize=13, fontweight="bold", pad=12,
    )
    ax.tick_params(colors=_FG, labelsize=9)
    ax.set_ylabel(f"Amount ({currency})", color=_FG, fontsize=10)
    for spine in ax.spines.values():
        spine.set_color(_GRID)
    ax.yaxis.grid(True, color=_GRID, linestyle="--", linewidth=0.5)
    ax.set_axisbelow(True)

    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=_BG)
    plt.close(fig)
    buf.seek(0)
    return buf


@restricted
async def dividends_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch and display dividend history with monthly chart."""
    if not update.message:
        return

    await update.message.reply_text("⏳ Fetching dividend data, hang tight...")
    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        async with GhostfolioClient(
            settings.ghostfolio_url, settings.ghostfolio_access_token
        ) as gf:
            result = await gf.get_orders()
    except GhostfolioError as e:
        logger.error("Ghostfolio API error: %s", e)
        await update.message.reply_text(f"Error connecting to Ghostfolio: {e.detail}")
        return
    except Exception:
        logger.exception("Unexpected error in /dividends")
        await update.message.reply_text("Unexpected error fetching dividend data.")
        return

    activities: list[dict[str, Any]] = result.get("activities", [])
    dividends = [o for o in activities if o.get("type") == "DIVIDEND"]

    if not dividends:
        await update.message.reply_text("No dividend records found.")
        return

    now = datetime.now(UTC)

    # Determine base currency: most common among dividends
    currency_counts: dict[str, int] = defaultdict(int)
    for div in dividends:
        currency_counts[div.get("currency", "EUR")] += 1
    base_currency = max(currency_counts, key=lambda c: currency_counts[c])

    # Aggregate
    monthly: dict[str, float] = defaultdict(float)  # YYYY-MM → amount (base currency)
    this_month_by_symbol: dict[str, float] = defaultdict(float)
    this_year_by_symbol: dict[str, float] = defaultdict(float)
    this_month_total = 0.0
    this_year_total = 0.0

    for div in dividends:
        dt = _parse_date(div["date"])
        amt = _amount(div)
        monthly[dt.strftime("%Y-%m")] += amt

        if dt.year == now.year and dt.month == now.month:
            this_month_total += amt
            this_month_by_symbol[_symbol_name(div)] += amt
        if dt.year == now.year:
            this_year_total += amt
            this_year_by_symbol[_symbol_name(div)] += amt

    lines: list[str] = ["💰 <b>Dividends</b>\n"]

    # This month
    month_name = now.strftime("%B")
    lines.append(f"<b>{month_name} (this month):</b> {_fmt(this_month_total, base_currency)}")
    if this_month_by_symbol:
        for name, amt in sorted(this_month_by_symbol.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"  · {name}: {_fmt(amt, base_currency)}")
    else:
        lines.append("  No dividends yet this month")

    lines.append("")

    # This year
    lines.append(f"<b>{now.year} (this year):</b> {_fmt(this_year_total, base_currency)}")
    if this_year_by_symbol:
        top = sorted(this_year_by_symbol.items(), key=lambda x: x[1], reverse=True)[:10]
        for name, amt in top:
            lines.append(f"  · {name}: {_fmt(amt, base_currency)}")
        if len(this_year_by_symbol) > 10:
            others = sum(v for _, v in list(this_year_by_symbol.items())[10:])
            extra = len(this_year_by_symbol) - 10
            lines.append(f"  · +{extra} more: {_fmt(others, base_currency)}")
    else:
        lines.append("  No dividends this year")

    # All-time total
    all_time = sum(monthly.values())
    lines.append(f"\n<b>All time:</b> {_fmt(all_time, base_currency)}")

    text = "\n".join(lines)

    try:
        chart_buf = _build_chart(dict(monthly), base_currency)
        if len(text) <= 1024:
            await update.message.reply_photo(
                photo=chart_buf, caption=text, parse_mode="HTML"
            )
        else:
            await update.message.reply_photo(photo=chart_buf)
            await update.message.reply_text(text, parse_mode="HTML")
        return
    except Exception:
        logger.exception("Failed to generate dividend chart")

    await update.message.reply_text(text, parse_mode="HTML")
