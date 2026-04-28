"""Parser for IBKR (Interactive Brokers) Dividends CSV exports.

Header: "Type","SettleDate","ISIN","Description","Amount","CurrencyPrimary"

Type mapping:
  Dividends                   → DIVIDEND
  Payment In Lieu Of Dividends → DIVIDEND
  Withholding Tax             → fee, matched to its dividend record
  Everything else             → SKIP

Matching logic:
  Withholding Tax rows share their description prefix (up to " - ") with the
  corresponding Dividend row (up to " ("). Both prefixes resolve to the same
  base string, e.g. "VT(US9220427424) CASH DIVIDEND USD 0.6504 PER SHARE".
  If a match is found the tax amount (abs) becomes the fee on the dividend.

Symbol extraction:
  Ticker is embedded in the description: "TICKER(ISIN) CASH DIVIDEND …"
  or "TICKER(80486947) …" for some ETFs. Regex captures the part before "(…)".

Quantity extraction:
  Description contains "N.NNNN PER SHARE". quantity = amount / price_per_share.
  Falls back to quantity=1, unit_price=amount when the pattern is absent.

Reference: examples/Export-To-Ghostfolio/src/converters/ibkrConverter.ts
Sample:    examples/Export-To-Ghostfolio/samples/ibkr-dividends-export.csv
"""

from __future__ import annotations

import contextlib
import logging
import re
from datetime import datetime

from bot.parsers.base import BrokerParser, GhostfolioActivity, register_parser

logger = logging.getLogger(__name__)

_DIVIDEND_TYPES = {"Dividends", "Payment In Lieu Of Dividends"}
_TAX_TYPE = "Withholding Tax"

_TICKER_RE = re.compile(r"^([\w.]+)\(")
_PRICE_PER_SHARE_RE = re.compile(r"(\d+\.?\d*)\s+PER\s+SHARE", re.IGNORECASE)


def _parse_float(raw: str) -> float:
    return float(raw.strip().strip('"'))


def _parse_date(raw: str) -> datetime | None:
    raw = raw.strip().strip('"')
    try:
        return datetime.strptime(raw, "%Y%m%d")
    except ValueError:
        return None


def _description_prefix(description: str, record_type: str) -> str:
    """Return the base part of the description used as the matching key."""
    if record_type == _TAX_TYPE:
        # "… PER SHARE - US TAX" → "… PER SHARE"
        return description.split(" - ")[0].strip()
    # "… PER SHARE (Ordinary Dividend)" → "… PER SHARE"
    return description.split(" (")[0].strip()


def _extract_ticker(description: str) -> str:
    m = _TICKER_RE.match(description.strip().strip('"'))
    return m.group(1) if m else ""


def _compute_quantity_and_price(amount: float, description: str) -> tuple[float, float]:
    """Return (quantity, unit_price) derived from the dividend amount and description."""
    m = _PRICE_PER_SHARE_RE.search(description)
    if m:
        price_per_share = float(m.group(1))
        if price_per_share > 0:
            quantity = round(amount / price_per_share, 4)
            return quantity, price_per_share
    return 1.0, amount


@register_parser
class IbkrDividendsParser(BrokerParser):
    name = "IBKR Dividends"
    slug = "ibkr-dividends"
    HEADER_SIGNATURE = (
        '"Type","SettleDate","ISIN","Description","Amount","CurrencyPrimary"'
    )
    DELIMITER = ","

    def parse(self, csv_content: str) -> list[GhostfolioActivity]:
        rows = self._read_csv(csv_content)

        # First pass: collect withholding-tax rows keyed by (isin, desc_prefix)
        tax_map: dict[tuple[str, str], float] = {}
        for row in rows:
            row_type = row.get("Type", "").strip().strip('"')
            if row_type != _TAX_TYPE:
                continue
            isin = row.get("ISIN", "").strip().strip('"')
            desc = row.get("Description", "").strip().strip('"')
            key = (isin, _description_prefix(desc, _TAX_TYPE))
            with contextlib.suppress(Exception):
                tax_map[key] = abs(_parse_float(row.get("Amount", "0")))

        # Second pass: produce DIVIDEND activities
        activities: list[GhostfolioActivity] = []
        for i, row in enumerate(rows):
            try:
                activity = self._parse_row(row, tax_map)
                if activity:
                    activities.append(activity)
            except Exception:
                logger.warning("Skipping IBKR Dividends row %d: %s", i + 2, row)

        logger.info(
            "Parsed %d activities from IBKR Dividends (%d rows total)",
            len(activities),
            len(rows),
        )
        return activities

    def _parse_row(
        self, row: dict[str, str], tax_map: dict[tuple[str, str], float]
    ) -> GhostfolioActivity | None:
        row_type = row.get("Type", "").strip().strip('"')
        if row_type not in _DIVIDEND_TYPES:
            return None

        isin = row.get("ISIN", "").strip().strip('"')
        desc = row.get("Description", "").strip().strip('"')
        currency = row.get("CurrencyPrimary", "").strip().strip('"')
        if currency == "GBX":
            currency = "GBp"

        amount = abs(_parse_float(row.get("Amount", "0")))
        ticker = _extract_ticker(desc)
        if not ticker and not isin:
            return None

        symbol = ticker if ticker else isin
        quantity, unit_price = _compute_quantity_and_price(amount, desc)

        key = (isin, _description_prefix(desc, row_type))
        fee = tax_map.get(key, 0.0)

        date = _parse_date(row.get("SettleDate", ""))
        if not date:
            logger.warning("Could not parse IBKR SettleDate: %s", row.get("SettleDate"))
            return None

        return GhostfolioActivity(
            currency=currency,
            data_source="YAHOO",
            date=date,
            fee=fee,
            quantity=quantity,
            symbol=symbol,
            type="DIVIDEND",
            unit_price=unit_price,
        )
