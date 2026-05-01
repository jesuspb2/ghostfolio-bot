"""Handler for /performance command."""

from __future__ import annotations

import asyncio
import io
import logging

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
_RED = "#ff453a"
_GRID = "#3a3a3c"

_RANGES: list[tuple[str, str]] = [
    ("1d", "Today"),
    ("wtd", "This week"),
    ("mtd", "This month"),
    ("ytd", "YTD"),
    ("1y", "1 year"),
    ("max", "All time"),
]


def _sym(currency: str) -> str:
    return {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency, f"{currency} ")


def _fmt_signed(value: float, currency: str) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}{_sym(currency)}{abs(value):,.2f}"


def _fmt_abbrev(value: float, currency: str) -> str:
    """Abbreviated signed monetary value: K for thousands, M for millions."""
    sign = "+" if value >= 0 else "-"
    s = _sym(currency)
    a = abs(value)
    if a >= 1_000_000:
        return f"{sign}{s}{a / 1_000_000:.1f}M"
    if a >= 1_000:
        return f"{sign}{s}{a / 1_000:.1f}K"
    return f"{sign}{s}{a:,.2f}"


def _fmt_pct(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value * 100:.2f}%"


def _build_chart(
    labels: list[str],
    pcts: list[float],
    gains: list[float],
    currency: str,
) -> io.BytesIO:
    """Horizontal bar chart of performance % per period."""
    colors = [_GREEN if p >= 0 else _RED for p in pcts]

    fig, ax = plt.subplots(figsize=(9, 5))
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)

    pct_display = [p * 100 for p in pcts]
    bars = ax.barh(labels, pct_display, color=colors, edgecolor=_BG, linewidth=0.5)

    # Label on each bar: pct + abbreviated amount
    for bar, p, g in zip(bars, pcts, gains, strict=True):
        w = bar.get_width()
        abbrev = _fmt_abbrev(g, currency)
        label = f"  {_fmt_pct(p)}  {abbrev}"
        x_pos = w
        ha = "left" if w >= 0 else "right"
        # nudge label slightly outside the bar
        offset = max(abs(v) for v in pct_display) * 0.02 if any(pct_display) else 0.5
        ax.text(
            x_pos + (offset if w >= 0 else -offset),
            bar.get_y() + bar.get_height() / 2,
            label,
            va="center", ha=ha, fontsize=9, color=_FG,
        )

    ax.axvline(0, color=_FG, linewidth=0.6, linestyle="--", alpha=0.4)
    ax.set_title("Performance by Period", color=_FG, fontsize=13, fontweight="bold", pad=12)
    ax.set_xlabel("Return (%)", color=_FG, fontsize=10)
    ax.tick_params(colors=_FG, labelsize=10)
    for spine in ax.spines.values():
        spine.set_color(_GRID)
    ax.xaxis.grid(True, color=_GRID, linestyle="--", linewidth=0.5)
    ax.set_axisbelow(True)

    # Extra right margin so labels don't get clipped
    x_max = max(abs(v) for v in pct_display) if pct_display else 1
    ax.set_xlim(left=-x_max * 1.05, right=x_max * 1.6)

    plt.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=_BG)
    plt.close(fig)
    buf.seek(0)
    return buf


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

    summary = details.get("summary", {})
    currency = summary.get("currency", "EUR")

    # Collect data points for chart and caption
    chart_labels: list[str] = []
    chart_pcts: list[float] = []
    chart_gains: list[float] = []

    for (range_key, label), res in zip(_RANGES, perf_results, strict=True):
        if isinstance(res, BaseException):
            logger.warning("Failed to fetch range %s: %s", range_key, res)
            chart_labels.append(label)
            chart_pcts.append(0.0)
            chart_gains.append(0.0)
            continue
        perf = res.get("performance", {})
        chart_labels.append(label)
        chart_pcts.append(perf.get("netPerformancePercentage", 0.0))
        chart_gains.append(perf.get("netPerformance", 0.0))

    # Build one-line caption
    all_time_idx = next((i for i, (r, _) in enumerate(_RANGES) if r == "max"), -1)
    all_time_pct = _fmt_pct(chart_pcts[all_time_idx]) if all_time_idx >= 0 else "N/A"
    ann = summary.get("annualizedPerformancePercent")
    ann_part = f"  ·  Annualized <b>{_fmt_pct(ann)}</b>" if ann is not None else ""
    caption = f"📈 <b>Performance</b>  ·  All time <b>{all_time_pct}</b>{ann_part}"

    try:
        chart_buf = _build_chart(chart_labels, chart_pcts, chart_gains, currency)
        await update.message.reply_photo(photo=chart_buf, caption=caption, parse_mode="HTML")
        return
    except Exception:
        logger.exception("Failed to generate performance chart")

    # Fallback: abbreviated text table
    col1, col2 = 11, 22
    sep = "─" * (col1 + col2 + 1)
    rows = [f"{'Period':<{col1}} {'Gain (%)':>{col2}}", sep]
    for label, gain, pct in zip(chart_labels, chart_gains, chart_pcts, strict=True):
        combined = f"{_fmt_abbrev(gain, currency)} ({_fmt_pct(pct)})"
        rows.append(f"{label:<{col1}} {combined:>{col2}}")
    if ann is not None:
        rows.append(sep)
        rows.append(f"{'Annualized':<{col1}} {_fmt_pct(ann):>{col2}}")
    table = "\n".join(rows)
    text = f"📈 <b>Performance</b>\n\n<code>{table}</code>"
    await update.message.reply_text(text, parse_mode="HTML")
