"""Async Ghostfolio API client using httpx.

Handles authentication (bearer token refresh) and exposes the endpoints
needed by the bot: import activities, get portfolio details, export data.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any, cast

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
        """Exchange the access token for a short-lived bearer token.

        Ghostfolio has two auth endpoints depending on version:
        - Newer: POST /api/v1/auth/anonymous  body={"accessToken": "..."}  → 200 or 201
        - Older: GET  /api/v1/auth/anonymous/{token}                        → 200
        We try POST first; fall back to GET if the server returns 404.
        """
        logger.debug("Authenticating with Ghostfolio at %s", self._base_url)
        resp = await self.http.post(
            "/auth/anonymous",
            json={"accessToken": self._access_token},
        )
        if resp.status_code == 404:
            logger.debug("POST /auth/anonymous returned 404, trying GET endpoint")
            resp = await self.http.get(f"/auth/anonymous/{self._access_token}")

        if resp.status_code not in (200, 201):
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
        return cast(dict[str, Any], await self._request("GET", "/portfolio/details"))

    async def portfolio_performance(self, range_: str = "max") -> dict[str, Any]:
        """GET /api/v1/portfolio/performance — returns performance for a time range.

        range_ accepted values: 1d, wtd, mtd, ytd, 1y, 5y, max
        """
        logger.info("Fetching portfolio performance (range=%s)", range_)
        url = f"{self._base_url}/api/v2/portfolio/performance?range={range_}"
        return cast(dict[str, Any], await self._request("GET", url))

    async def portfolio_holdings(self) -> dict[str, Any]:
        """GET /api/v1/portfolio/holdings — current positions."""
        logger.info("Fetching portfolio holdings")
        return cast(dict[str, Any], await self._request("GET", "/portfolio/holdings"))

    # -- Orders / Activities ---------------------------------------------------

    async def get_orders(self, account_id: str | None = None) -> dict[str, Any]:
        """GET /api/v1/order — list all activities, optionally filtered by account."""
        logger.info("Fetching orders (account_id=%s)", account_id or "all")
        params = {"accounts": account_id} if account_id else None
        return cast(dict[str, Any], await self._request("GET", "/order", params=params))

    async def validate_activities(
        self, activities: list[dict[str, Any]], chunk_size: int = 25
    ) -> list[str]:
        """POST /api/v1/import?dryRun=true — validate without persisting.

        Sends activities in chunks (same limit as real import) and collects any
        error messages from Ghostfolio.  Returns a list of error strings; an
        empty list means all activities are valid.
        """
        logger.info("Dry-run validating %d activities", len(activities))
        chunks = [activities[i : i + chunk_size] for i in range(0, len(activities), chunk_size)]

        async def _validate_chunk(chunk: list[dict[str, Any]]) -> str | None:
            try:
                await self._request(
                    "POST", "/import", params={"dryRun": "true"}, json={"activities": chunk}
                )
                return None
            except GhostfolioError as e:
                return e.detail[:200]

        results = await asyncio.gather(*[_validate_chunk(c) for c in chunks])
        errors = [r for r in results if r is not None]

        if errors:
            logger.warning("Dry-run validation found %d error(s): %s", len(errors), errors)
        else:
            logger.info("Dry-run validation passed for all activities")
        return errors

    async def import_activities(
        self, activities: list[dict[str, Any]], chunk_size: int = 25
    ) -> tuple[int, list[str]]:
        """POST /api/v1/import — bulk import activities.

        Ghostfolio.io caps bulk imports at 25 activities per request; self-hosted
        instances have no limit.  The default chunk_size=25 is safe for both.

        When a chunk fails because a symbol is not found on the data source,
        retries each activity individually so the rest can still be imported.

        Returns (imported_count, skipped_symbols).
        """
        logger.info("Importing %d activities into Ghostfolio", len(activities))
        logger.info("Import payload (first 3): %s", activities[:3])
        chunks = [activities[i : i + chunk_size] for i in range(0, len(activities), chunk_size)]
        total_created = 0
        skipped: list[str] = []

        async def _import_single(activity: dict[str, Any]) -> tuple[int, str | None]:
            try:
                resp = await self._request("POST", "/import", json={"activities": [activity]})
                if isinstance(resp, dict) and "activities" in resp:
                    return len(resp["activities"]), None
                return 1, None
            except GhostfolioError as inner:
                _inner_retryable = inner.status_code == 500 or (
                    inner.status_code == 400
                    and "is not valid for the specified data source" in inner.detail
                )
                if _inner_retryable:
                    symbol = activity.get("symbol", "?")
                    logger.warning(
                        "Skipping activity (status=%d): %s — %s",
                        inner.status_code, symbol, inner.detail[:120],
                    )
                    return 0, symbol
                raise

        for chunk in chunks:
            try:
                resp = await self._request("POST", "/import", json={"activities": chunk})
                logger.info("Ghostfolio /import response: %s", resp)
                if isinstance(resp, dict) and "activities" in resp:
                    total_created += len(resp["activities"])
                else:
                    total_created += len(chunk)
            except GhostfolioError as e:
                _is_retryable = e.status_code == 500 or (
                    e.status_code == 400
                    and "is not valid for the specified data source" in e.detail
                )
                if _is_retryable:
                    # Retry concurrently so valid activities still get imported
                    retry_results = await asyncio.gather(*[_import_single(a) for a in chunk])
                    for count, sym in retry_results:
                        total_created += count
                        if sym:
                            skipped.append(sym)
                else:
                    raise

        if skipped:
            logger.warning("Skipped %d activities with unknown symbols: %s", len(skipped), skipped)
        logger.info("Successfully imported %d/%d activities", total_created, len(activities))
        return total_created, skipped

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
        data = cast(dict[str, Any], await self._request("GET", "/account"))
        result = data.get("accounts", data) if isinstance(data, dict) else data
        return cast(list[dict[str, Any]], result)

    async def create_account(
        self, *, name: str, currency: str, balance: float = 0.0
    ) -> dict[str, Any]:
        """POST /api/v1/account — create a new account."""
        logger.info("Creating account: %s (%s)", name, currency)
        payload = {
            "balance": balance,
            "currency": currency,
            "isExcluded": False,
            "name": name,
            "platformId": None,
        }
        return cast(dict[str, Any], await self._request("POST", "/account", json=payload))

    # -- Export / Backup -------------------------------------------------------

    async def export_data(self) -> dict[str, Any]:
        """GET /api/v1/export — full JSON export of all activities."""
        logger.info("Exporting full Ghostfolio data")
        return cast(dict[str, Any], await self._request("GET", "/export"))


# -- Deduplication -------------------------------------------------------------

def build_manual_symbol_map(existing_activities: list[dict[str, Any]]) -> dict[str, str]:
    """Build a mapping from human symbol (ISIN/name) → SymbolProfile UUID.

    Ghostfolio stores MANUAL activities internally with a UUID as
    SymbolProfile.symbol and the original imported string as SymbolProfile.name.
    When re-importing or deduplicating, we must use the UUID, not the ISIN.
    """
    symbol_map: dict[str, str] = {}
    for a in existing_activities:
        sp = a.get("SymbolProfile") or {}
        if sp.get("dataSource") != "MANUAL":
            continue
        name = sp.get("name", "")   # the ISIN/string we originally imported
        uuid = sp.get("symbol", "")  # the UUID Ghostfolio assigned internally
        if name and uuid and name != uuid:
            symbol_map[name] = uuid
    if symbol_map:
        logger.info("Built MANUAL symbol map: %s", symbol_map)
    return symbol_map


def resolve_manual_symbols(
    activities: list[dict[str, Any]],
    symbol_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Replace ISIN/human symbols with their Ghostfolio UUIDs for MANUAL activities.

    Mutates activities in-place and returns the list for convenience.
    Ghostfolio requires the SymbolProfile UUID (not the original ISIN) when
    importing MANUAL activities that already have a SymbolProfile.
    """
    for a in activities:
        if a.get("dataSource") == "MANUAL":
            isin = a.get("symbol", "")
            uuid = symbol_map.get(isin)
            if uuid:
                a["symbol"] = uuid
    return activities


def _existing_key(activity: dict[str, Any]) -> tuple[str, str, str, float, str]:
    """Stable key for an activity already stored in Ghostfolio.

    For MANUAL activities Ghostfolio stores the UUID in SymbolProfile.symbol.
    We use the UUID consistently so new activities (after symbol resolution)
    produce the same key.
    """
    sp = activity.get("SymbolProfile") or {}
    if sp.get("dataSource") == "MANUAL":
        # UUID is in sp.symbol; fallback to name then top-level symbol
        symbol = sp.get("symbol") or sp.get("name") or activity.get("symbol", "")
    else:
        symbol = sp.get("symbol") or sp.get("name") or activity.get("symbol", "")
    return (
        activity.get("date", "")[:10],
        symbol,
        activity.get("type", ""),
        round(float(activity.get("quantity", 0)), 4),
        activity.get("accountId", ""),
    )


def deduplicate_activities(
    new_activities: list[dict[str, Any]],
    existing_activities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return only the activities not already present in Ghostfolio.

    Keyed on date (date-only) + symbol UUID + type + quantity + accountId.
    Call resolve_manual_symbols() on new_activities before this function so
    MANUAL symbols use UUIDs that match what Ghostfolio returns.
    """
    existing_keys = {_existing_key(a) for a in existing_activities}

    def _new_key(a: dict[str, Any]) -> tuple[str, str, str, float, str]:
        return (
            a.get("date", "")[:10],
            a.get("symbol", ""),
            a.get("type", ""),
            round(float(a.get("quantity", 0)), 4),
            a.get("accountId", ""),
        )

    result = [a for a in new_activities if _new_key(a) not in existing_keys]
    logger.info(
        "Dedup: %d existing, %d new → %d to import (%d duplicates skipped)",
        len(existing_activities),
        len(new_activities),
        len(result),
        len(new_activities) - len(result),
    )
    return result
