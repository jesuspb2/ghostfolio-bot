"""Parser for Revolut Invest (stocks/ETFs) CSV exports.

Header: Date,Ticker,Type,Quantity,Price per share,Total Amount,Currency,FX Rate

Type mapping:
  BUY - MARKET / BUY - LIMIT / STOCK SPLIT → BUY
  SELL - MARKET / SELL - LIMIT             → SELL
  DIVIDEND                                 → DIVIDEND
  CUSTODY FEE                              → FEE
  CASH TOP-UP / CASH WITHDRAWAL / TRANSFER → SKIP

Reference: examples/Export-To-Ghostfolio/src/converters/revolutConverter.ts
Sample:    examples/Export-To-Ghostfolio/samples/revolut-invest-export.csv
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from bot.parsers.base import BrokerParser, GhostfolioActivity, register_parser

logger = logging.getLogger(__name__)

_SKIP_TYPES = ("CASH TOP-UP", "CASH WITHDRAWAL", "TRANSFER")
_CURRENCY_SYMBOLS = re.compile(r"[$€£]")

_FEE_SYMBOL = "REVOLUT-CUSTODY-FEE"


def _parse_amount(raw: str) -> float:
    """Parse monetary values like '$52.07', '€88.94', '-$0.01', '$0'. Dot = decimal."""
    raw = raw.strip()
    if not raw:
        return 0.0
    cleaned = _CURRENCY_SYMBOLS.sub("", raw).replace(",", "").strip()
    return float(cleaned) if cleaned else 0.0


def _parse_quantity(raw: str) -> float:
    """Parse quantity, handling European decimal comma: '0,76672417' → 0.76672417."""
    raw = raw.strip().strip('"')
    if not raw:
        return 0.0
    if "." in raw:
        return float(raw.replace(",", ""))
    if "," in raw:
        return float(raw.replace(",", "."))
    return float(raw)


@register_parser
class RevolutInvestParser(BrokerParser):
    name = "Revolut Invest"
    slug = "revolut-invest"
    HEADER_SIGNATURE = "Date,Ticker,Type,Quantity,Price per share,Total Amount,Currency,FX Rate"
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
                logger.warning("Skipping Revolut Invest row %d: %s", i + 2, row)

        logger.info(
            "Parsed %d activities from Revolut Invest (%d rows total)",
            len(activities),
            len(rows),
        )
        return activities

    def _parse_row(self, row: dict[str, str]) -> GhostfolioActivity | None:
        raw_type = row.get("Type", "").strip()
        type_upper = raw_type.upper()

        if any(skip in type_upper for skip in _SKIP_TYPES):
            return None

        if "BUY" in type_upper or "STOCK SPLIT" in type_upper:
            activity_type = "BUY"
        elif "SELL" in type_upper:
            activity_type = "SELL"
        elif "DIVIDEND" in type_upper:
            activity_type = "DIVIDEND"
        elif "FEE" in type_upper:
            activity_type = "FEE"
        else:
            logger.debug("Skipping unknown Revolut Invest type '%s'", raw_type)
            return None

        ticker = row.get("Ticker", "").strip()
        currency = row.get("Currency", "EUR").strip()
        if currency == "GBX":
            currency = "GBp"

        raw_qty = row.get("Quantity", "").strip()
        raw_price = row.get("Price per share", "").strip()
        raw_total = row.get("Total Amount", "").strip()

        quantity_val = _parse_quantity(raw_qty) if raw_qty else 0.0
        price_val = _parse_amount(raw_price)
        total_val = _parse_amount(raw_total)

        if activity_type == "FEE":
            symbol = _FEE_SYMBOL
            data_source = "MANUAL"
            quantity = 1.0
            unit_price = abs(total_val)
        elif activity_type == "DIVIDEND":
            if not ticker:
                return None
            symbol = ticker
            data_source = "YAHOO"
            quantity = 1.0
            unit_price = abs(total_val)
        elif activity_type in ("BUY", "SELL"):
            if not ticker:
                return None
            symbol = ticker
            data_source = "YAHOO"
            quantity = quantity_val
            unit_price = price_val if price_val else (
                abs(total_val) / quantity if quantity else 0.0
            )
            if quantity == 0:
                return None
        else:
            return None

        date = self._parse_date(row.get("Date", "").strip())
        if not date:
            logger.warning("Could not parse Revolut Invest date: %s", row.get("Date"))
            return None

        return GhostfolioActivity(
            currency=currency,
            data_source=data_source,
            date=date,
            fee=0.0,
            quantity=quantity,
            symbol=symbol,
            type=activity_type,
            unit_price=unit_price,
        )

    def _parse_date(self, date_str: str) -> datetime | None:
        if not date_str:
            return None
        # ISO 8601: "2023-09-22T13:30:10.514Z"
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.astimezone(UTC).replace(tzinfo=None)
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None
