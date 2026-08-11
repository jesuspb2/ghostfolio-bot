"""Parser for IBKR Cash Report Flex Query XML reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from xml.etree import ElementTree


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_date(raw: str) -> date:
    for date_format in ("%Y%m%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.strip(), date_format).date()
        except ValueError:
            continue
    raise ValueError("Invalid IBKR Cash Report date")


@dataclass(frozen=True, slots=True)
class IbkrCashBalance:
    """Base-currency cash balance at the end of an IBKR statement day."""

    report_date: date
    ending_cash: float
    ending_settled_cash: float


class IbkrCashReportParser:
    """Extract the base-currency summary from an IBKR Cash Report."""

    def can_handle(self, xml_content: str) -> bool:
        try:
            root = ElementTree.fromstring(xml_content)
        except ElementTree.ParseError:
            return False

        if _local_name(root.tag) != "FlexQueryResponse":
            return False
        return any(_local_name(element.tag) == "CashReport" for element in root.iter())

    def parse(self, xml_content: str) -> IbkrCashBalance:
        root = ElementTree.fromstring(xml_content)
        base_summaries = [
            element.attrib
            for element in root.iter()
            if _local_name(element.tag) == "CashReportCurrency"
            and element.attrib.get("currency", "").strip().upper() == "BASE_SUMMARY"
        ]
        if len(base_summaries) != 1:
            raise ValueError(
                "IBKR Cash Report must contain exactly one BASE_SUMMARY record"
            )

        summary = base_summaries[0]
        return IbkrCashBalance(
            report_date=_parse_date(summary.get("toDate", "")),
            ending_cash=float(summary.get("endingCash", "")),
            ending_settled_cash=float(summary.get("endingSettledCash", "")),
        )
