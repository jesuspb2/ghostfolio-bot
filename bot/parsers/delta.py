"""Parser for Delta portfolio tracker CSV exports.

Handles STOCK and CRYPTO (fiat-quoted) BUY/SELL/DIVIDEND records.
CRYPTO records with a fiat quote currency (EUR, USD, GBP, ...) are imported
as "{BASE}-{QUOTE}" Yahoo Finance symbols (e.g. BTC-EUR, dataSource=YAHOO).
Cross-crypto records (Quote currency is also a crypto) are skipped.
DEPOSIT, WITHDRAW, and TRANSFER rows are always ignored.

Reference: examples/Export-To-Ghostfolio/src/converters/deltaConverter.ts
Sample:    examples/Export-To-Ghostfolio/samples/delta-export.csv
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from bot.parsers.base import BrokerParser, GhostfolioActivity, register_parser

logger = logging.getLogger(__name__)

_STRIP_PARENS = re.compile(r"\s*\(.*?\)\s*$")  # removes " (Full Name)" suffix
_SKIP_WAYS = ("DEPOSIT", "WITHDRAW", "TRANSFER")
_WAY_MAP = {"BUY": "BUY", "SELL": "SELL", "DIVIDEND": "DIVIDEND"}
_FIAT_CURRENCIES = {
    "EUR", "USD", "GBP", "CHF", "JPY", "CAD", "AUD", "NZD", "SEK", "NOK",
    "DKK", "HKD", "SGD", "MXN", "BRL", "CNY", "INR", "KRW", "TRY", "ZAR",
    "GBp", "GBX",
}


@register_parser
class DeltaParser(BrokerParser):
    name = "Delta"
    slug = "delta"
    HEADER_SIGNATURE = "Date,Way,Base amount,Base currency (name),Base type,Quote amount,Quote currency,Exchange,Sent/Received from,Sent to,Fee amount,Fee currency (name),Broker,Notes"
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
                logger.warning("Skipping Delta row %d due to parse error: %s", i + 2, row)

        logger.info("Parsed %d activities from Delta (%d rows total)", len(activities), len(rows))
        return activities

    def _parse_row(self, row: dict[str, str]) -> GhostfolioActivity | None:
        way = row.get("Way", "").strip().upper()
        base_type = row.get("Base type", "").strip().upper()

        if any(skip in way for skip in _SKIP_WAYS):
            return None

        # Map action (substring match to handle e.g. "Buy" / "BUY")
        activity_type: str | None = None
        for key, val in _WAY_MAP.items():
            if key in way:
                activity_type = val
                break
        if activity_type is None:
            logger.debug("Skipping unknown Delta action '%s'", way)
            return None

        # GBX (pence as traded on LSE) → GBp (Yahoo Finance convention)
        currency = row.get("Quote currency", "").strip()
        if currency == "GBX":
            currency = "GBp"

        if base_type == "CRYPTO":
            # Only import crypto when quoted in fiat (e.g. BTC/EUR → symbol "BTC-EUR")
            # Cross-crypto pairs (ETH/BTC) have no clean Yahoo Finance fiat price → skip
            if currency not in _FIAT_CURRENCIES:
                logger.debug(
                    "Skipping cross-crypto record %s/%s",
                    row.get("Base currency (name)"),
                    currency,
                )
                return None

        # Symbol: strip " (Full Name)" suffix that Delta appends
        raw_symbol = row.get("Base currency (name)", "").strip()
        symbol = _STRIP_PARENS.sub("", raw_symbol).strip()
        if not symbol:
            return None

        if base_type == "CRYPTO":
            symbol = f"{symbol}-{currency}"

        try:
            base_amount = float(row.get("Base amount", "") or 0)
            quote_amount = float(row.get("Quote amount", "") or 0)
            fee_amount = float(row.get("Fee amount", "") or 0)
        except ValueError:
            logger.warning("Invalid numeric field in Delta row: %s", row)
            return None

        if activity_type == "DIVIDEND":
            quantity = 1.0
            unit_price = abs(quote_amount)
        else:
            quantity = base_amount
            if quantity == 0:
                return None
            unit_price = round(quote_amount / quantity, 6)

        date = self._parse_date(row.get("Date", "").strip())
        if not date:
            logger.warning("Could not parse Delta date '%s'", row.get("Date"))
            return None

        return GhostfolioActivity(
            currency=currency,
            data_source="YAHOO",
            date=date,
            fee=fee_amount,
            quantity=quantity,
            symbol=symbol,
            type=activity_type,
            unit_price=unit_price,
            comment=row.get("Notes", "").strip(),
        )

    def _parse_date(self, date_str: str) -> datetime | None:
        if not date_str:
            return None
        # Delta exports ISO 8601 with timezone offset: "2023-06-02 16:06:47-04:00"
        try:
            dt = datetime.fromisoformat(date_str)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None
