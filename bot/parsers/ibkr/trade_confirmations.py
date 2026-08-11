"""Parser for IBKR Trade Confirmation Flex Query XML reports."""

from __future__ import annotations

import logging
from datetime import datetime
from xml.etree import ElementTree

from bot.parsers.base import GhostfolioActivity

logger = logging.getLogger(__name__)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_date(raw: str) -> datetime | None:
    for date_format in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), date_format)
        except ValueError:
            continue
    return None


def _parse_float(raw: str) -> float:
    return float(raw.strip())


class IbkrTradeConfirmationsParser:
    """Convert individual IBKR execution confirmations into Ghostfolio activities."""

    def can_handle(self, xml_content: str) -> bool:
        try:
            root = ElementTree.fromstring(xml_content)
        except ElementTree.ParseError:
            return False

        if _local_name(root.tag) != "FlexQueryResponse":
            return False
        return any(_local_name(element.tag) == "TradeConfirms" for element in root.iter())

    def parse(self, xml_content: str) -> list[GhostfolioActivity]:
        root = ElementTree.fromstring(xml_content)
        activities: list[GhostfolioActivity] = []

        records = [
            element
            for element in root.iter()
            if _local_name(element.tag) == "TradeConfirm"
        ]
        for index, record in enumerate(records, start=1):
            try:
                activity = self._parse_record(record.attrib)
                if activity:
                    activities.append(activity)
            except (TypeError, ValueError):
                logger.warning("Skipping invalid IBKR TradeConfirm record %d", index)

        logger.info(
            "Parsed %d activities from IBKR Trade Confirmations (%d records total)",
            len(activities),
            len(records),
        )
        return activities

    def _parse_record(self, record: dict[str, str]) -> GhostfolioActivity | None:
        action = record.get("buySell", "").strip().upper()
        if action not in {"BUY", "SELL"}:
            return None

        # Currency-conversion executions (for example EUR.USD) have no ISIN and
        # are not portfolio activities Ghostfolio can import.
        isin = record.get("isin", "").strip()
        if not isin:
            return None

        quantity = abs(_parse_float(record.get("quantity", "0")))
        if quantity == 0:
            return None

        trade_date = _parse_date(record.get("tradeDate", ""))
        if trade_date is None:
            logger.warning("Could not parse IBKR TradeConfirm tradeDate")
            return None

        currency = record.get("currency", "").strip()
        if currency == "GBX":
            currency = "GBp"

        trade_id = record.get("tradeID", "").strip()
        execution_id = record.get("execID", "").strip()
        references = [
            reference
            for reference in (
                f"TradeID={trade_id}" if trade_id else "",
                f"ExecID={execution_id}" if execution_id else "",
            )
            if reference
        ]
        comment = "IBKR Trade Confirmation"
        if references:
            comment += "; " + "; ".join(references)

        return GhostfolioActivity(
            currency=currency,
            data_source="YAHOO",
            date=trade_date,
            fee=abs(_parse_float(record.get("commission", "0"))),
            quantity=quantity,
            symbol=isin,
            type=action,
            unit_price=abs(_parse_float(record.get("price", "0"))),
            comment=comment,
        )
