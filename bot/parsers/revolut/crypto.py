"""Parser for Revolut Crypto CSV exports.

Header: Symbol,Type,Quantity,Price,Value,Fees,Date

Type mapping:
  Buy            → BUY
  Sell           → SELL
  Staking reward → DIVIDEND
  Learn reward   → INTEREST
  Send / Receive / Stake / Exchange → SKIP

Symbol is constructed as {CRYPTO}-{FIAT} (e.g. BTC-EUR), dataSource=YAHOO.
Yahoo Finance supports crypto pairs like BTC-EUR, ZKJ-EUR, etc.
Price, Value and Fees fields embed the currency symbol/code:
  "89,162.28 SEK", "€35,761.54", "$105,058.55"

Reference: examples/Export-To-Ghostfolio/src/converters/revolutConverter.ts
Sample:    examples/Export-To-Ghostfolio/samples/revolut-crypto-export.csv
"""

from __future__ import annotations

import logging
from datetime import datetime

from bot.parsers.base import BrokerParser, GhostfolioActivity, register_parser

logger = logging.getLogger(__name__)

_SYMBOL_MAP = {"€": "EUR", "$": "USD", "£": "GBP"}
_SKIP_TYPES = ("send", "receive", "stake", "exchange")


def _parse_currency_value(raw: str) -> tuple[float, str]:
    """Parse '89,162.28 SEK', '€10.00', '$0.24' → (amount, currency_code)."""
    raw = raw.strip()
    if not raw:
        return 0.0, ""

    for sym, code in _SYMBOL_MAP.items():
        if raw.startswith(sym):
            return float(raw[len(sym) :].replace(",", "")), code

    # Suffix code: "89,162.28 SEK"
    parts = raw.rsplit(" ", 1)
    if len(parts) == 2:
        num_str, code = parts
        return float(num_str.replace(",", "")), code

    return float(raw.replace(",", "")), ""


@register_parser
class RevolutCryptoParser(BrokerParser):
    name = "Revolut Crypto"
    slug = "revolut-crypto"
    HEADER_SIGNATURE = "Symbol,Type,Quantity,Price,Value,Fees,Date"
    DELIMITER = ","

    def parse(self, csv_content: str) -> list[GhostfolioActivity]:
        rows = self._read_csv(csv_content)
        activities: list[GhostfolioActivity] = []

        for i, row in enumerate(rows):
            try:
                activity = self._parse_row(row)
                if activity:
                    activities.append(activity)
            except Exception:
                logger.warning("Skipping Revolut Crypto row %d: %s", i + 2, row)

        logger.info(
            "Parsed %d activities from Revolut Crypto (%d rows total)",
            len(activities),
            len(rows),
        )
        return activities

    def _parse_row(self, row: dict[str, str]) -> GhostfolioActivity | None:
        crypto_symbol = row.get("Symbol", "").strip()
        raw_type = row.get("Type", "").strip()
        type_lower = raw_type.lower()

        if any(skip in type_lower for skip in _SKIP_TYPES):
            return None

        if "buy" in type_lower:
            activity_type = "BUY"
        elif "sell" in type_lower:
            activity_type = "SELL"
        elif "staking reward" in type_lower:
            activity_type = "DIVIDEND"
        elif "learn reward" in type_lower or "reward" in type_lower:
            activity_type = "INTEREST"
        else:
            logger.debug("Skipping unknown Revolut Crypto type '%s'", raw_type)
            return None

        try:
            quantity = float(row.get("Quantity", "0") or "0")
        except ValueError:
            return None

        price_val, price_currency = _parse_currency_value(row.get("Price", ""))
        value_val, value_currency = _parse_currency_value(row.get("Value", ""))
        fee_val, _ = _parse_currency_value(row.get("Fees", ""))

        currency = price_currency or value_currency or "EUR"
        ghostfolio_symbol = f"{crypto_symbol}-{currency}"

        if activity_type in ("DIVIDEND", "INTEREST"):
            unit_price = value_val if value_val else 0.0
            quantity_out = 1.0
        else:
            quantity_out = quantity
            unit_price = price_val
            if quantity_out == 0:
                return None

        date = self._parse_date(row.get("Date", "").strip())
        if not date:
            logger.warning("Could not parse Revolut Crypto date: %s", row.get("Date"))
            return None

        return GhostfolioActivity(
            currency=currency,
            data_source="YAHOO",
            date=date,
            fee=fee_val,
            quantity=quantity_out,
            symbol=ghostfolio_symbol,
            type=activity_type,
            unit_price=unit_price,
        )

    def _parse_date(self, date_str: str) -> datetime | None:
        if not date_str:
            return None
        # "May 5, 2020, 10:10:57 PM"
        for fmt in (
            "%b %d, %Y, %I:%M:%S %p",
            "%b %d, %Y, %I:%M %p",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None
