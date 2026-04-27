"""Async Ghostfolio API client using httpx.

Handles authentication (bearer token refresh) and exposes the endpoints
needed by the bot: import activities, get portfolio details, export data.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class GhostfolioError(Exception):
    """Raised when the Ghostfolio API returns a 4xx or 5xx response."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Ghostfolio API error {status_code}: {detail}")


class GhostfolioClient:
    """Async client for the Ghostfolio REST API.

    Usage::

        async with GhostfolioClient(url, access_token) as gf:
            details = await gf.portfolio_details()
    """

    def __init__(self, base_url: str, access_token: str, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._access_token = access_token
        self._timeout = timeout
        self._bearer_token: str | None = None
        self._http: httpx.AsyncClient | None = None

    # -- Lifecycle -------------------------------------------------------------

    async def __aenter__(self) -> GhostfolioClient:
        self._http = httpx.AsyncClient(
            base_url=f"{self._base_url}/api/v1",
            timeout=self._timeout,
        )
        await self._authenticate()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("GhostfolioClient not initialised — use 'async with'")
        return self._http

    # -- Auth ------------------------------------------------------------------

    async def _authenticate(self) -> None:
        """Exchange the access token for a short-lived bearer token."""
        logger.debug("Authenticating with Ghostfolio at %s", self._base_url)
        resp = await self.http.post(
            "/auth/anonymous",
            json={"accessToken": self._access_token},
        )
        if resp.status_code != 201:
            raise GhostfolioError(resp.status_code, "Authentication failed")
        data = resp.json()
        self._bearer_token = data.get("authToken")
        logger.info("Authenticated with Ghostfolio (token acquired)")

    def _auth_headers(self) -> dict[str, str]:
        if not self._bearer_token:
            raise RuntimeError("Not authenticated")
        return {"Authorization": f"Bearer {self._bearer_token}"}

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> Any:
        """Make an authenticated request, retrying auth once on 401."""
        resp = await self.http.request(
            method, path, headers=self._auth_headers(), **kwargs
        )

        if resp.status_code == 401:
            logger.info("Bearer token expired — re-authenticating and retrying %s %s", method, path)
            await self._authenticate()
            resp = await self.http.request(
                method, path, headers=self._auth_headers(), **kwargs
            )

        if resp.status_code >= 400:
            raise GhostfolioError(resp.status_code, resp.text)

        # Some endpoints (e.g. /import) respond 201 with no body
        if resp.status_code == 201 and not resp.text.strip():
            return None

        return resp.json() if resp.text.strip() else None

    # -- Portfolio -------------------------------------------------------------

    async def portfolio_details(self) -> dict[str, Any]:
        """GET /api/v1/portfolio/details — full portfolio overview."""
        logger.info("Fetching portfolio details")
        return await self._request("GET", "/portfolio/details")

    async def portfolio_performance(self, range_: str = "max") -> dict[str, Any]:
        """GET /api/v1/portfolio/performance — returns performance for a time range.

        range_ accepted values: 1d, wtd, mtd, ytd, 1y, 5y, max
        """
        logger.info("Fetching portfolio performance (range=%s)", range_)
        return await self._request(
            "GET", "/portfolio/performance", params={"range": range_}
        )

    async def portfolio_holdings(self) -> dict[str, Any]:
        """GET /api/v1/portfolio/holdings — current positions."""
        logger.info("Fetching portfolio holdings")
        return await self._request("GET", "/portfolio/holdings")

    # -- Orders / Activities ---------------------------------------------------

    async def get_orders(self, account_id: str | None = None) -> dict[str, Any]:
        """GET /api/v1/order — list all activities, optionally filtered by account."""
        logger.info("Fetching orders (account_id=%s)", account_id or "all")
        params = {"accounts": account_id} if account_id else None
        return await self._request("GET", "/order", params=params)

    async def import_activities(self, activities: list[dict[str, Any]]) -> None:
        """POST /api/v1/import — bulk import activities.

        Each activity dict must contain:
            currency, dataSource, date, fee, quantity, symbol, type, unitPrice
        """
        logger.info("Importing %d activities into Ghostfolio", len(activities))
        await self._request("POST", "/import", json={"activities": activities})
        logger.info("Successfully imported %d activities", len(activities))

    async def add_activity(
        self,
        *,
        symbol: str,
        type_: str,
        quantity: float,
        unit_price: float,
        currency: str = "USD",
        fee: float = 0.0,
        date: datetime | None = None,
        data_source: str = "YAHOO",
        account_id: str,
    ) -> None:
        """Import a single activity — convenience wrapper around import_activities."""
        if date is None:
            date = datetime.now(UTC)

        logger.info("Adding single activity: %s %s x%s @ %s", type_, symbol, quantity, unit_price)
        activity = {
            "accountId": account_id,
            "currency": currency,
            "dataSource": data_source,
            "date": date.strftime("%Y-%m-%dT00:00:00.000Z"),
            "fee": fee,
            "quantity": quantity,
            "symbol": symbol,
            "type": type_,
            "unitPrice": unit_price,
        }
        await self.import_activities([activity])

    # -- Accounts --------------------------------------------------------------

    async def get_accounts(self) -> list[dict[str, Any]]:
        """GET /api/v1/account — list all accounts."""
        logger.info("Fetching accounts")
        data = await self._request("GET", "/account")
        return data.get("accounts", data) if isinstance(data, dict) else data

    # -- Export / Backup -------------------------------------------------------

    async def export_data(self) -> dict[str, Any]:
        """GET /api/v1/export — full JSON export of all activities."""
        logger.info("Exporting full Ghostfolio data")
        return await self._request("GET", "/export")
