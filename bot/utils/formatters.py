"""Format Ghostfolio data into Telegram-friendly messages."""

from __future__ import annotations

from typing import Any


def format_currency(value: float, currency: str = "EUR") -> str:
    """Format a number as currency."""
    symbols = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF "}
    symbol = symbols.get(currency, f"{currency} ")
    return f"{symbol}{value:,.2f}"


def format_percentage(value: float) -> str:
    """Format a decimal as percentage with sign."""
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def format_portfolio_summary(details: dict[str, Any]) -> str:
    """Build a Markdown summary from portfolio details response."""
    summary = details.get("summary", {})
    holdings = details.get("holdings", {})

    total_value = summary.get("currentValueInBaseCurrency", 0)
    total_investment = summary.get("committedFunds", 0)
    net_performance = summary.get("netPerformance", 0)
    net_performance_pct = summary.get("netPerformancePercentage", 0) * 100

    currency = summary.get("currency", "EUR")

    lines = [
        "*Portfolio Summary*",
        "",
        f"Total value: `{format_currency(total_value, currency)}`",
        f"Invested: `{format_currency(total_investment, currency)}`",
        f"P&L: `{format_currency(net_performance, currency)}`"
        f" ({format_percentage(net_performance_pct)})",
        "",
    ]

    if holdings:
        sorted_holdings = sorted(
            holdings.values() if isinstance(holdings, dict) else holdings,
            key=lambda h: h.get("valueInBaseCurrency", h.get("value", 0)),
            reverse=True,
        )

        lines.append("*Top Holdings:*")
        for h in sorted_holdings[:10]:
            # MANUAL symbols use a UUID as key; prefer human-readable name
            symbol = h.get("name") or h.get("symbol", "?")
            value = h.get("valueInBaseCurrency", h.get("value", 0))
            alloc = h.get("allocationInPercentage", 0) * 100
            lines.append(f"  {symbol} — `{format_currency(value, currency)}` ({alloc:.1f}%)")

    return "\n".join(lines)


def format_activity_confirm(
    symbol: str,
    type_: str,
    quantity: float,
    unit_price: float,
    fee: float,
    currency: str,
    date: str,
    data_source: str = "YAHOO",
) -> str:
    """Format an activity for user confirmation before import."""
    return (
        "*Confirm transaction:*\n\n"
        f"Type: `{type_}`\n"
        f"Symbol: `{symbol}`\n"
        f"Data source: `{data_source}`\n"
        f"Quantity: `{quantity}`\n"
        f"Price: `{format_currency(unit_price, currency)}`\n"
        f"Fee: `{format_currency(fee, currency)}`\n"
        f"Date: `{date}`\n\n"
        "Send /confirm to import or /cancel to discard."
    )
