"""Parser for Revolut Savings CSV exports.

Maps INTEREST/CASHBACK rows to DIVIDEND activities with dataSource=MANUAL.
Rows with State != COMPLETED are skipped. TOPUP (deposits/withdrawals) are
ignored as they are cash movements, not investment activities.
"""

from __future__ import annotations

import logging
from datetime import datetime

from bot.parsers.base import BrokerParser, GhostfolioActivity, register_parser

logger = logging.getLogger(__name__)

# Columns that identify this as a Revolut Savings CSV
_EXPECTED_HEADERS = {"Type", "Product", "Started Date", "Amount", "Currency"}


@register_parser
class RevolutSavingsParser(BrokerParser):
    name = "Revolut Savings"
    slug = "revolut-savings"

    def can_handle(self, csv_content: str) -> bool:
        first_line = csv_content.split("\n")[0].strip()
        headers = {h.strip().strip('"') for h in first_line.split(",")}
        return _EXPECTED_HEADERS.issubset(headers)

    def parse(self, csv_content: str) -> list[GhostfolioActivity]:
        rows = self._read_csv(csv_content)
        activities: list[GhostfolioActivity] = []

        for row in rows:
            activity = self._parse_row(row)
            if activity:
                activities.append(activity)

        logger.info(
            "Parsed %d activities from Revolut Savings (%d rows total)",
            len(activities),
            len(rows),
        )
        return activities

    def _parse_row(self, row: dict[str, str]) -> GhostfolioActivity | None:
        tx_type = row.get("Type", "").strip()
        product = row.get("Product", "").strip()
        description = row.get("Description", "").strip()
        currency = row.get("Currency", "EUR").strip()
        state = row.get("State", "").strip()

        # Skip non-completed transactions
        if state and state.upper() != "COMPLETED":
            return None

        # Parse amount
        try:
            amount = float(row.get("Amount", "0").replace(",", "."))
        except ValueError:
            logger.warning("Invalid amount in row: %s", row)
            return None

        # Parse date — Revolut uses several formats
        date_str = row.get("Completed Date", "") or row.get("Started Date", "")
        date = self._parse_date(date_str.strip())
        if not date:
            logger.warning("Could not parse date '%s' in row: %s", date_str, row)
            return None

        if tx_type.upper() in ("CASHBACK", "INTEREST"):
            return GhostfolioActivity(
                currency=currency,
                data_source="MANUAL",
                date=date,
                fee=0.0,
                quantity=1,
                symbol=self._make_symbol(product, currency),
                type="DIVIDEND",
                unit_price=abs(amount),
                comment=f"Revolut Savings: {description}",
            )

        # TOPUP and other cash movements are not investment activities
        return None

    def _make_symbol(self, product: str, currency: str) -> str:
        clean = product.upper().replace(" ", "-")[:20] if product else "SAVINGS"
        return f"REVOLUT-{clean}-{currency}"

    def _parse_date(self, date_str: str) -> datetime | None:
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%d %b %Y",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None
