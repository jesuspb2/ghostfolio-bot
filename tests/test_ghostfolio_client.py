"""Tests for the Ghostfolio async client."""

import pytest
import respx
from httpx import Response

from bot.ghostfolio_client import GhostfolioClient, GhostfolioError

BASE_URL = "http://ghostfolio:3333"
ACCESS_TOKEN = "test-token"


@pytest.fixture
def mock_api():
    with respx.mock(base_url=f"{BASE_URL}/api/v1") as mock:
        mock.post("/auth/anonymous").mock(
            return_value=Response(201, json={"authToken": "bearer-xyz"})
        )
        yield mock


@pytest.mark.asyncio
async def test_authenticate(mock_api):
    async with GhostfolioClient(BASE_URL, ACCESS_TOKEN) as gf:
        assert gf._bearer_token == "bearer-xyz"


@pytest.mark.asyncio
async def test_context_manager_closes_client(mock_api):
    async with GhostfolioClient(BASE_URL, ACCESS_TOKEN) as gf:
        assert gf._http is not None
    assert gf._http is None


@pytest.mark.asyncio
async def test_portfolio_details(mock_api):
    mock_api.get("/portfolio/details").mock(
        return_value=Response(200, json={
            "summary": {"currentValue": 10000, "investment": 8000},
            "holdings": {},
        })
    )

    async with GhostfolioClient(BASE_URL, ACCESS_TOKEN) as gf:
        details = await gf.portfolio_details()
        assert details["summary"]["currentValue"] == 10000


@pytest.mark.asyncio
async def test_portfolio_holdings(mock_api):
    mock_api.get("/portfolio/holdings").mock(
        return_value=Response(200, json={"holdings": {"MSFT": {"value": 500}}})
    )

    async with GhostfolioClient(BASE_URL, ACCESS_TOKEN) as gf:
        data = await gf.portfolio_holdings()
        assert "holdings" in data


@pytest.mark.asyncio
async def test_portfolio_performance():
    with respx.mock() as mock:
        mock.post(f"{BASE_URL}/api/v1/auth/anonymous").mock(
            return_value=Response(201, json={"authToken": "bearer-xyz"})
        )
        mock.get(f"{BASE_URL}/api/v2/portfolio/performance").mock(
            return_value=Response(200, json={"performance": {"currentValue": 10000}})
        )
        async with GhostfolioClient(BASE_URL, ACCESS_TOKEN) as gf:
            data = await gf.portfolio_performance(range_="ytd")
            assert "performance" in data


@pytest.mark.asyncio
async def test_get_orders(mock_api):
    mock_api.get("/order").mock(
        return_value=Response(200, json={"activities": []})
    )

    async with GhostfolioClient(BASE_URL, ACCESS_TOKEN) as gf:
        data = await gf.get_orders()
        assert "activities" in data


@pytest.mark.asyncio
async def test_get_accounts(mock_api):
    mock_api.get("/account").mock(
        return_value=Response(200, json={"accounts": [{"id": "abc"}]})
    )

    async with GhostfolioClient(BASE_URL, ACCESS_TOKEN) as gf:
        accounts = await gf.get_accounts()
        assert isinstance(accounts, list)
        assert accounts[0]["id"] == "abc"


@pytest.mark.asyncio
async def test_import_activities(mock_api):
    mock_api.post("/import").mock(return_value=Response(201, text=""))

    async with GhostfolioClient(BASE_URL, ACCESS_TOKEN) as gf:
        await gf.import_activities([{
            "currency": "EUR",
            "dataSource": "YAHOO",
            "date": "2024-01-01T00:00:00.000Z",
            "fee": 0,
            "quantity": 10,
            "symbol": "VWCE.DE",
            "type": "BUY",
            "unitPrice": 100.0,
        }])


@pytest.mark.asyncio
async def test_export_data(mock_api):
    mock_api.get("/export").mock(
        return_value=Response(200, json={"activities": [{"symbol": "MSFT"}]})
    )

    async with GhostfolioClient(BASE_URL, ACCESS_TOKEN) as gf:
        data = await gf.export_data()
        assert len(data["activities"]) == 1


@pytest.mark.asyncio
async def test_auth_retry_on_401(mock_api):
    """A 401 response triggers re-authentication and a transparent retry."""
    call_count = 0

    def portfolio_side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return Response(401, text="Unauthorized")
        return Response(200, json={"summary": {}, "holdings": {}})

    mock_api.get("/portfolio/details").mock(side_effect=portfolio_side_effect)

    async with GhostfolioClient(BASE_URL, ACCESS_TOKEN) as gf:
        details = await gf.portfolio_details()
        assert details is not None
        assert call_count == 2


@pytest.mark.asyncio
async def test_error_raises_ghostfolio_error(mock_api):
    mock_api.get("/portfolio/details").mock(
        return_value=Response(500, text="Internal Server Error")
    )

    async with GhostfolioClient(BASE_URL, ACCESS_TOKEN) as gf:
        with pytest.raises(GhostfolioError) as exc_info:
            await gf.portfolio_details()
        assert exc_info.value.status_code == 500
        assert exc_info.value.detail == "Internal Server Error"


@pytest.mark.asyncio
async def test_error_raises_on_4xx(mock_api):
    mock_api.get("/export").mock(
        return_value=Response(403, text="Forbidden")
    )

    async with GhostfolioClient(BASE_URL, ACCESS_TOKEN) as gf:
        with pytest.raises(GhostfolioError) as exc_info:
            await gf.export_data()
        assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_auth_failure_raises_ghostfolio_error():
    with respx.mock(base_url=f"{BASE_URL}/api/v1") as mock:
        mock.post("/auth/anonymous").mock(
            return_value=Response(401, text="Invalid token")
        )
        with pytest.raises(GhostfolioError) as exc_info:
            async with GhostfolioClient(BASE_URL, "bad-token"):
                pass
        assert exc_info.value.status_code == 401
