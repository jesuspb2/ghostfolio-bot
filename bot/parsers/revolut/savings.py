"""Parser for Revolut Savings account CSV exports.

Header: Date,Description,"Value, EUR",Price per share,Quantity of shares

Row types mapped to Ghostfolio:
  BUY ...                 → BUY   (cash deposit or interest reinvestment)
  Interest PAID ...       → DIVIDEND (daily interest income)
  Service Fee Charged ... → FEE
  Interest Reinvested ... → SKIP  (paired with a BUY the next day, avoid double-count)
  Anything else           → SKIP

The ISIN embedded in every Description is used as symbol (dataSource=MANUAL).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from bot.parsers.base import BrokerParser, GhostfolioActivity, register_parser

logger = logging.getLogger(__name__)

_ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")
_CURRENCY_RE = re.compile(r"\b(EUR|USD|GBP|CHF|SEK|NOK|DKK|CAD|AUD|JPY|GBp)\b")

# "Value, EUR" is the column name after csv.DictReader strips outer quotes
_VALUE_COL = "Value, EUR"


def _parse_amount(raw: str) -> float:
    """Parse amounts like '3,200', '-0.5142', '1.2242'. Comma = thousands sep."""
    return float(raw.replace(",", "")) if raw.strip() else 0.0


@register_parser
class RevolutSavingsParser(BrokerParser):
    name = "Revolut Savings"
    slug = "revolut-savings"
    HEADER_SIGNATURE = 'Date,Description,"Value, EUR",Price per share,Quantity of shares'
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
                logger.warning("Skipping Revolut Savings row %d: %s", i + 2, row)

        logger.info(
            "Parsed %d activities from Revolut Savings (%d rows total)",
            len(activities),
            len(rows),
        )
        return activities

    def _parse_row(self, row: dict[str, str]) -> GhostfolioActivity | None:
        description = row.get("Description", "").strip()
        desc_upper = description.upper()

        if desc_upper.startswith("BUY"):
            activity_type = "BUY"
        elif "INTEREST PAID" in desc_upper:
            activity_type = "DIVIDEND"
        elif "SERVICE FEE CHARGED" in desc_upper:
            activity_type = "FEE"
        elif "INTEREST REINVESTED" in desc_upper:
            return None  # paired with a BUY — skip to avoid double-counting
        elif desc_upper.startswith("SELL"):
            activity_type = "SELL"
        else:
            logger.debug("Unrecognised Revolut Savings description: %s", description)
            return None

        isin_match = _ISIN_RE.search(description)
        if not isin_match:
            logger.warning("No ISIN found in description: %s", description)
            return None
        symbol = isin_match.group(1)

        currency_match = _CURRENCY_RE.search(description)
        currency = currency_match.group(1) if currency_match else "EUR"

        raw_value = row.get(_VALUE_COL, "").strip()
        value = _parse_amount(raw_value)

        raw_price = row.get("Price per share", "").strip()
        raw_qty = row.get("Quantity of shares", "").strip()
        price_per_share = _parse_amount(raw_price) if raw_price else 0.0
        quantity_of_shares = _parse_amount(raw_qty) if raw_qty else 0.0

        if activity_type in ("BUY", "SELL"):
            if quantity_of_shares <= 0 or price_per_share <= 0:
                logger.warning("BUY/SELL with missing quantity or price: %s", row)
                return None
            quantity = quantity_of_shares
            unit_price = price_per_share
        else:
            quantity = 1.0
            unit_price = abs(value)

        date = self._parse_date(row.get("Date", "").strip())
        if not date:
            logger.warning("Could not parse date in row: %s", row)
            return None

        return GhostfolioActivity(
            currency=currency,
            data_source="MANUAL",
            date=date,
            fee=0.0,
            quantity=quantity,
            symbol=symbol,
            type=activity_type,
            unit_price=unit_price,
            comment=description,
        )

    def _parse_date(self, date_str: str) -> datetime | None:
        if not date_str:
            return None
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
