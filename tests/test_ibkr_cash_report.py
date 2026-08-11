"""Tests for IBKR Cash Report Flex Query XML reports."""

import pytest

from bot.parsers.ibkr.cash_report import IbkrCashReportParser

CASH_REPORT_XML = """\
<FlexQueryResponse queryName="Cash" type="AF">
  <FlexStatements count="1"><FlexStatement accountId="hidden"><CashReport>
    <CashReportCurrency currency="BASE_SUMMARY" toDate="20260810"
      endingCash="17.253492428" endingSettledCash="17.253492428" />
    <CashReportCurrency currency="EUR" toDate="20260810"
      endingCash="17.253492428" endingSettledCash="17.253492428" />
    <CashReportCurrency currency="USD" toDate="20260810"
      endingCash="0" endingSettledCash="0" />
  </CashReport></FlexStatement></FlexStatements>
</FlexQueryResponse>
"""


def test_parses_base_currency_cash_balance() -> None:
    parser = IbkrCashReportParser()

    assert parser.can_handle(CASH_REPORT_XML)
    balance = parser.parse(CASH_REPORT_XML)

    assert balance.report_date.isoformat() == "2026-08-10"
    assert balance.ending_cash == pytest.approx(17.253492428)
    assert balance.ending_settled_cash == pytest.approx(17.253492428)


def test_requires_base_summary() -> None:
    parser = IbkrCashReportParser()
    xml = CASH_REPORT_XML.replace('currency="BASE_SUMMARY"', 'currency="GBP"')

    with pytest.raises(ValueError, match="BASE_SUMMARY"):
        parser.parse(xml)
