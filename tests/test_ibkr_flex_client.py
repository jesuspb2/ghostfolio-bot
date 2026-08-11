"""Tests for the IBKR Flex Web Service client."""

from datetime import date

import pytest
import respx
from httpx import Response

from bot.ibkr_flex_client import IbkrFlexClient, IbkrFlexError

BASE_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
SUCCESS_XML = """\
<FlexStatementResponse>
  <Status>Success</Status>
  <ReferenceCode>987654321</ReferenceCode>
</FlexStatementResponse>
"""
PENDING_XML = """\
<FlexStatementResponse>
  <Status>Fail</Status>
  <ErrorCode>1019</ErrorCode>
  <ErrorMessage>Statement generation in progress.</ErrorMessage>
</FlexStatementResponse>
"""
TRADES_CSV = (
    '"Buy/Sell","TradeDate","ISIN","Quantity","TradePrice","TradeMoney",'
    '"CurrencyPrimary","IBCommission","IBCommissionCurrency"\n'
    '"BUY","20260801","US0378331005","1","200","200","USD","-1","USD"\n'
)


@pytest.mark.asyncio
@respx.mock
async def test_fetch_csv_generates_polls_and_returns_statement() -> None:
    send = respx.get(f"{BASE_URL}/SendRequest").mock(
        return_value=Response(200, text=SUCCESS_XML)
    )
    get = respx.get(f"{BASE_URL}/GetStatement").mock(
        side_effect=[Response(200, text=PENDING_XML), Response(200, text=TRADES_CSV)]
    )

    client = IbkrFlexClient(
        "secret-token",
        "123456",
        poll_interval=0,
        max_poll_attempts=2,
    )
    result = await client.fetch_csv(
        from_date=date(2026, 8, 1),
        to_date=date(2026, 8, 11),
    )

    assert result == TRADES_CSV
    assert send.call_count == 1
    assert get.call_count == 2
    assert send.calls[0].request.url.params["t"] == "secret-token"
    assert send.calls[0].request.url.params["q"] == "123456"
    assert send.calls[0].request.url.params["fd"] == "20260801"
    assert send.calls[0].request.url.params["td"] == "20260811"
    assert get.calls[0].request.url.params["q"] == "987654321"
    assert send.calls[0].request.headers["user-agent"].startswith(
        "ghostfolio-companion-bot/"
    )


@pytest.mark.asyncio
@respx.mock
async def test_service_error_does_not_expose_token() -> None:
    expired_xml = """\
    <FlexStatementResponse>
      <Status>Fail</Status>
      <ErrorCode>1012</ErrorCode>
      <ErrorMessage>Token has expired.</ErrorMessage>
    </FlexStatementResponse>
    """
    respx.get(f"{BASE_URL}/SendRequest").mock(
        return_value=Response(200, text=expired_xml)
    )

    client = IbkrFlexClient("do-not-log-me", "123456")
    with pytest.raises(IbkrFlexError) as exc_info:
        await client.fetch_csv()

    assert exc_info.value.code == "1012"
    assert "Token has expired" in str(exc_info.value)
    assert "do-not-log-me" not in str(exc_info.value)


@pytest.mark.asyncio
@respx.mock
async def test_poll_timeout_raises_1019() -> None:
    respx.get(f"{BASE_URL}/SendRequest").mock(
        return_value=Response(200, text=SUCCESS_XML)
    )
    respx.get(f"{BASE_URL}/GetStatement").mock(
        return_value=Response(200, text=PENDING_XML)
    )

    client = IbkrFlexClient(
        "secret-token",
        "123456",
        poll_interval=0,
        max_poll_attempts=2,
    )
    with pytest.raises(IbkrFlexError) as exc_info:
        await client.fetch_csv()

    assert exc_info.value.code == "1019"


@pytest.mark.asyncio
async def test_rejects_more_than_365_calendar_days() -> None:
    client = IbkrFlexClient("secret-token", "123456")

    with pytest.raises(ValueError, match="cannot exceed 365 days"):
        await client.fetch_csv(
            from_date=date(2025, 8, 11),
            to_date=date(2026, 8, 11),
        )
