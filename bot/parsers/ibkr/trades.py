"""Parser for IBKR (Interactive Brokers) Trades CSV exports.

Header: "Buy/Sell","TradeDate","ISIN","Quantity","TradePrice","TradeMoney",
        "CurrencyPrimary","IBCommission","IBCommissionCurrency"

Type mapping:
  BUY  → BUY
  SELL → SELL
  Rows with empty ISIN (currency conversions) → SKIP

Notes:
  - IBCommission is exported as a negative number; take abs() for the fee.
  - Quantity for SELL is negative; take abs().
  - GBX currency → GBp (Yahoo Finance convention).
  - Symbol is the ISIN; Ghostfolio resolves it via Yahoo Finance (dataSource=YAHOO).

Reference: examples/Export-To-Ghostfolio/src/converters/ibkrConverter.ts
Sample:    examples/Export-To-Ghostfolio/samples/ibkr-trades-export.csv
"""

from __future__ import annotations

import logging
from datetime import datetime

from bot.parsers.base import BrokerParser, GhostfolioActivity, register_parser

logger = logging.getLogger(__name__)


def _parse_float(raw: str) -> float:
    return float(raw.strip().strip('"'))


def _parse_date(raw: str) -> datetime | None:
    """Parse IBKR date format YYYYMMDD."""
    raw = raw.strip().strip('"')
    try:
        return datetime.strptime(raw, "%Y%m%d")
    except ValueError:
        return None


@register_parser
class IbkrTradesParser(BrokerParser):
    name = "IBKR Trades"
    slug = "ibkr-trades"
    HEADER_SIGNATURE = (
        '"Buy/Sell","TradeDate","ISIN","Quantity","TradePrice","TradeMoney",'
        '"CurrencyPrimary","IBCommission","IBCommissionCurrency"'
    )
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
                logger.warning("Skipping IBKR Trades row %d: %s", i + 2, row)

        logger.info(
            "Parsed %d activities from IBKR Trades (%d rows total)",
            len(activities),
            len(rows),
        )
        return activities

    def _parse_row(self, row: dict[str, str]) -> GhostfolioActivity | None:
        action = row.get("Buy/Sell", "").strip().strip('"').upper()
        if action not in ("BUY", "SELL"):
            return None

        isin = row.get("ISIN", "").strip().strip('"')
        if not isin:
            return None

        currency = row.get("CurrencyPrimary", "").strip().strip('"')
        if currency == "GBX":
            currency = "GBp"

        quantity = abs(_parse_float(row.get("Quantity", "0")))
        unit_price = abs(_parse_float(row.get("TradePrice", "0")))
        fee = abs(_parse_float(row.get("IBCommission", "0")))

        if quantity == 0:
            return None

        date = _parse_date(row.get("TradeDate", ""))
        if not date:
            logger.warning("Could not parse IBKR TradeDate: %s", row.get("TradeDate"))
            return None

        return GhostfolioActivity(
            currency=currency,
            data_source="YAHOO",
            date=date,
            fee=fee,
            quantity=quantity,
            symbol=isin,
            type=action,
            unit_price=unit_price,
        )
