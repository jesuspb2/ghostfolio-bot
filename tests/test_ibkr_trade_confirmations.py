"""Tests for IBKR Trade Confirmation Flex Query XML reports."""

from bot.parsers.ibkr.trade_confirmations import IbkrTradeConfirmationsParser

TRADE_CONFIRMATIONS_XML = """\
<FlexQueryResponse queryName="Today" type="TCF">
  <FlexStatements count="1">
    <FlexStatement accountId="hidden">
      <TradeConfirms>
        <TradeConfirm buySell="BUY" tradeDate="20260811"
          isin="US0378331005" symbol="AAPL" quantity="2"
          price="225.50" currency="USD" commission="-0.35"
          commissionCurrency="USD" tradeID="123" execID="456" />
        <TradeConfirm buySell="SELL" tradeDate="2026-08-11"
          isin="US5949181045" symbol="MSFT" quantity="-1"
          price="400" currency="USD" commission="-0.40"
          commissionCurrency="USD" tradeID="789" execID="987" />
        <TradeConfirm buySell="SELL" tradeDate="20260811"
          isin="" symbol="EUR.USD" quantity="-100"
          price="1.15" currency="USD" commission="0"
          commissionCurrency="USD" tradeID="111" execID="222" />
      </TradeConfirms>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
"""


def test_parses_individual_trade_confirmations() -> None:
    parser = IbkrTradeConfirmationsParser()

    assert parser.can_handle(TRADE_CONFIRMATIONS_XML)
    activities = parser.parse(TRADE_CONFIRMATIONS_XML)

    assert len(activities) == 2
    assert activities[0].symbol == "US0378331005"
    assert activities[0].type == "BUY"
    assert activities[0].quantity == 2
    assert activities[0].unit_price == 225.5
    assert activities[0].fee == 0.35
    assert activities[0].comment == (
        "IBKR Trade Confirmation; TradeID=123; ExecID=456"
    )
    assert activities[1].type == "SELL"
    assert activities[1].quantity == 1


def test_rejects_non_trade_confirmation_xml() -> None:
    parser = IbkrTradeConfirmationsParser()

    assert not parser.can_handle("<FlexQueryResponse type='AF'/>")
