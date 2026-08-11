"""Tests for preparing an idempotent IBKR-to-Ghostfolio sync."""

from typing import Any

import pytest

from bot.ibkr_sync import (
    IbkrSyncError,
    prepare_ibkr_sync,
    select_ghostfolio_account,
)

TRADES_CSV = (
    '"Buy/Sell","TradeDate","ISIN","Quantity","TradePrice","TradeMoney",'
    '"CurrencyPrimary","IBCommission","IBCommissionCurrency"\n'
    '"BUY","20260801","US0378331005","1","200","200","USD","-1","USD"\n'
    '"BUY","20260802","US5949181045","2","400","800","USD","-1","USD"\n'
)

TRADE_CONFIRMATIONS_XML = """\
<FlexQueryResponse queryName="Today" type="TCF">
  <FlexStatements count="1"><FlexStatement accountId="hidden"><TradeConfirms>
    <TradeConfirm buySell="BUY" tradeDate="20260811" isin="US02079K3059"
      symbol="NOW" quantity="1" price="900" currency="USD" commission="-0.5"
      commissionCurrency="USD" tradeID="123" execID="456" />
  </TradeConfirms></FlexStatement></FlexStatements>
</FlexQueryResponse>
"""


class FakeGhostfolio:
    def __init__(self, existing: list[dict[str, Any]]) -> None:
        self.existing = existing
        self.validated: list[dict[str, Any]] = []

    async def get_orders(self, account_id: str | None = None) -> dict[str, Any]:
        return {"activities": self.existing}

    async def validate_activities(
        self, activities: list[dict[str, Any]], chunk_size: int = 25
    ) -> list[str]:
        self.validated = activities
        return []


@pytest.mark.asyncio
async def test_prepare_sync_resolves_deduplicates_and_validates(monkeypatch) -> None:
    async def fake_resolve(
        activities: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        mapping = {"US0378331005": "AAPL", "US5949181045": "MSFT"}
        for activity in activities:
            activity["symbol"] = mapping[activity["symbol"]]
        return activities, []

    monkeypatch.setattr("bot.ibkr_sync.resolve_symbols", fake_resolve)
    existing = [{
        "accountId": "ibkr-account",
        "date": "2026-08-01T00:00:00.000Z",
        "quantity": 1.0,
        "type": "BUY",
        "SymbolProfile": {"dataSource": "YAHOO", "symbol": "AAPL"},
    }]
    ghostfolio = FakeGhostfolio(existing)

    preview = await prepare_ibkr_sync(
        TRADES_CSV,
        "ibkr-account",
        ghostfolio,  # type: ignore[arg-type]
    )

    assert preview.report_activities == 2
    assert preview.duplicates_skipped == 1
    assert len(preview.to_import) == 1
    assert preview.to_import[0]["symbol"] == "MSFT"
    assert ghostfolio.validated == preview.to_import


@pytest.mark.asyncio
async def test_prepare_sync_accepts_empty_report(monkeypatch) -> None:
    async def fake_resolve(
        activities: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        return activities, []

    monkeypatch.setattr("bot.ibkr_sync.resolve_symbols", fake_resolve)
    header_only = TRADES_CSV.splitlines()[0] + "\n"
    ghostfolio = FakeGhostfolio([])

    preview = await prepare_ibkr_sync(
        header_only,
        "ibkr-account",
        ghostfolio,  # type: ignore[arg-type]
    )

    assert preview.report_activities == 0
    assert preview.to_import == []
    assert ghostfolio.validated == []


@pytest.mark.asyncio
async def test_prepare_sync_combines_historical_and_intraday(monkeypatch) -> None:
    async def fake_resolve(
        activities: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        mapping = {
            "US0378331005": "AAPL",
            "US5949181045": "MSFT",
            "US02079K3059": "NOW",
        }
        for activity in activities:
            activity["symbol"] = mapping[activity["symbol"]]
        return activities, []

    monkeypatch.setattr("bot.ibkr_sync.resolve_symbols", fake_resolve)
    ghostfolio = FakeGhostfolio([])

    preview = await prepare_ibkr_sync(
        TRADES_CSV,
        "ibkr-account",
        ghostfolio,  # type: ignore[arg-type]
        trade_confirmation_content=TRADE_CONFIRMATIONS_XML,
    )

    assert preview.historical_activities == 2
    assert preview.intraday_activities == 1
    assert preview.report_activities == 3
    assert len(preview.to_import) == 3
    assert preview.to_import[-1]["symbol"] == "NOW"


@pytest.mark.asyncio
async def test_prepare_sync_rejects_wrong_flex_format() -> None:
    ghostfolio = FakeGhostfolio([])

    with pytest.raises(IbkrSyncError, match="expected IBKR Trades CSV"):
        await prepare_ibkr_sync(
            "Symbol,Quantity\nAAPL,1\n",
            "ibkr-account",
            ghostfolio,  # type: ignore[arg-type]
        )


def test_select_ghostfolio_account_by_name() -> None:
    accounts = [
        {"id": "bitcoin-id", "name": "Bitcoin"},
        {"id": "ibkr-id", "name": "Interactive Brokers"},
    ]

    account_id, account_name = select_ghostfolio_account(
        accounts,
        account_id=None,
        account_name="interactive brokers",
    )

    assert account_id == "ibkr-id"
    assert account_name == "Interactive Brokers"


def test_select_ghostfolio_account_explicit_id_takes_precedence() -> None:
    accounts = [
        {"id": "first-id", "name": "Interactive Brokers"},
        {"id": "pinned-id", "name": "Pinned Account"},
    ]

    account_id, account_name = select_ghostfolio_account(
        accounts,
        account_id="pinned-id",
        account_name="Interactive Brokers",
    )

    assert account_id == "pinned-id"
    assert account_name == "Pinned Account"


def test_select_ghostfolio_account_rejects_missing_name() -> None:
    with pytest.raises(IbkrSyncError, match="was not found"):
        select_ghostfolio_account(
            [{"id": "bitcoin-id", "name": "Bitcoin"}],
            account_id=None,
            account_name="Interactive Brokers",
        )
