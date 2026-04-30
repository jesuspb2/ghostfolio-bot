"""Handler for /portfolio command."""

from __future__ import annotations

import io
import logging
from typing import Any

import matplotlib
import matplotlib.pyplot as plt
import seaborn as sns  # type: ignore[import-untyped]
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from bot.config import settings
from bot.ghostfolio_client import GhostfolioClient, GhostfolioError
from bot.utils.auth import restricted

matplotlib.use("Agg")  # non-interactive backend — no display needed

logger = logging.getLogger(__name__)

_BG = "#1c1c1e"
_FG = "#ebebf0"
_MAX_SLICES = 8


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



def _build_allocation_chart(
    holdings: dict[str, Any], total: float, currency: str
) -> io.BytesIO:
    """Return a PNG donut chart of portfolio allocation as an in-memory buffer."""
    sns.set_theme(style="dark")

    # Sort holdings by value descending
    sorted_h = sorted(
        holdings.items(),
        key=lambda kv: kv[1].get("valueInBaseCurrency", 0),
        reverse=True,
    )

    # Top N slices + "Others"
    top = sorted_h[:_MAX_SLICES]
    others_value = sum(v.get("valueInBaseCurrency", 0) for _, v in sorted_h[_MAX_SLICES:])

    labels: list[str] = []
    sizes: list[float] = []
    for _, h in top:
        labels.append(h.get("name") or h.get("symbol", "?"))
        sizes.append(h.get("valueInBaseCurrency", 0))
    if others_value > 0:
        labels.append("Others")
        sizes.append(others_value)

    palette = sns.color_palette("muted", len(sizes))

    fig, ax = plt.subplots(figsize=(9, 7))
    fig.patch.set_facecolor(_BG)
    ax.set_facecolor(_BG)

    wedges, _texts, autotexts = ax.pie(  # type: ignore[misc]
        sizes,
        labels=None,
        colors=palette,
        autopct="%1.1f%%",
        pctdistance=0.78,
        startangle=90,
        wedgeprops={"width": 0.55, "edgecolor": _BG, "linewidth": 2},
    )

    for at in autotexts:
        at.set_color(_FG)
        at.set_fontsize(9)

    # Center text: total portfolio value
    sym = {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency, currency + " ")
    ax.text(
        0, 0.07, f"{sym}{total:,.0f}",
        ha="center", va="center", fontsize=16, fontweight="bold", color=_FG,
    )
    ax.text(
        0, -0.15, "Total",
        ha="center", va="center", fontsize=10, color="#8e8e93",
    )

    # Legend on the right
    ax.legend(
        wedges,
        labels,
        loc="center left",
        bbox_to_anchor=(1.0, 0, 0.3, 1),
        frameon=False,
        labelcolor=_FG,
        fontsize=10,
    )

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor=_BG)
    plt.close(fig)
    buf.seek(0)
    return buf


@restricted
async def portfolio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fetch and display portfolio summary with allocation chart."""
    if not update.message:
        return

    await update.message.reply_text("⏳ Fetching your portfolio, hang tight...")
    await update.message.chat.send_action(ChatAction.TYPING)

    try:
        async with GhostfolioClient(
            settings.ghostfolio_url, settings.ghostfolio_access_token
        ) as gf:
            details = await gf.portfolio_details()

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

    # Build text summary
    lines = [f"💼 <b>Total: {_fmt_eur(total, currency)}</b>"]

    if holdings:
        sorted_h = sorted(
            holdings.items(),
            key=lambda kv: kv[1].get("valueInBaseCurrency", 0),
            reverse=True,
        )
        lines.append("")
        lines.append("📊 <b>Holdings:</b>")
        for key, h in sorted_h[:10]:
            name = h.get("name") or key
            value = h.get("valueInBaseCurrency", 0)
            alloc = h.get("allocationInPercentage", 0) * 100
            gain = h.get("netPerformance", 0)
            gain_pct = h.get("netPerformancePercent", 0)
            pl_label = "gain" if gain >= 0 else "loss"
            lines.append(f"   <b>{name}</b> ({alloc:.0f}%) — {_fmt_eur(value, currency)}")
            lines.append(f"   {pl_label}: {_fmt_signed(gain, currency)} ({_fmt_pct(gain_pct)})")

    text = "\n".join(lines)

    # Try to send chart + text as a single message (photo with caption).
    # Telegram caps captions at 1024 chars; fall back to photo-then-text if exceeded.
    if holdings:
        try:
            chart_buf = _build_allocation_chart(holdings, total, currency)
            if len(text) <= 1024:
                await update.message.reply_photo(
                    photo=chart_buf, caption=text, parse_mode="HTML"
                )
            else:
                await update.message.reply_photo(photo=chart_buf)
                await update.message.reply_text(text, parse_mode="HTML")
            return
        except Exception:
            logger.exception("Failed to generate allocation chart")

    await update.message.reply_text(text, parse_mode="HTML")
