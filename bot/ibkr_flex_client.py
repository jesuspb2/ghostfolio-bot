"""Async client for the read-only IBKR Flex Web Service."""

from __future__ import annotations

import asyncio
from datetime import date
from xml.etree import ElementTree

import httpx

_BASE_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService"
_RETRYABLE_STATEMENT_CODES = {
    "1001",
    "1003",
    "1004",
    "1005",
    "1006",
    "1007",
    "1008",
    "1009",
    "1019",
    "1021",
}


class IbkrFlexError(Exception):
    """Raised when IBKR cannot generate or return a Flex statement."""

    def __init__(self, detail: str, code: str | None = None) -> None:
        self.code = code
        self.detail = detail
        prefix = f"IBKR Flex error {code}" if code else "IBKR Flex error"
        super().__init__(f"{prefix}: {detail}")


def _element_text(root: ElementTree.Element, name: str) -> str | None:
    """Find an XML element by local name, with or without a namespace."""
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] == name:
            return element.text.strip() if element.text else None
    return None


def _parse_service_response(body: str) -> tuple[str | None, str | None, str | None] | None:
    """Return (status, reference code, error detail) for service XML responses."""
    if not body.lstrip().startswith("<"):
        return None
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return None

    if root.tag.rsplit("}", 1)[-1] != "FlexStatementResponse":
        return None

    status = _element_text(root, "Status")
    reference = _element_text(root, "ReferenceCode")
    error_code = _element_text(root, "ErrorCode")
    error_message = _element_text(root, "ErrorMessage")
    error_detail = None
    if error_code or error_message:
        error_detail = f"{error_code or 'unknown'}|{error_message or 'Unknown error'}"
    return status, reference, error_detail


class IbkrFlexClient:
    """Generate a preconfigured Flex Query and retrieve its CSV output.

    The Flex token is deliberately never included in logs or exception messages.
    """

    def __init__(
        self,
        token: str,
        query_id: str,
        *,
        user_agent: str = "ghostfolio-companion-bot/0.1",
        timeout: float = 30.0,
        poll_interval: float = 3.0,
        max_poll_attempts: int = 20,
    ) -> None:
        if not token.strip():
            raise ValueError("IBKR Flex token cannot be empty")
        if not query_id.strip():
            raise ValueError("IBKR Flex query ID cannot be empty")
        if max_poll_attempts < 1:
            raise ValueError("max_poll_attempts must be at least 1")

        self._token = token
        self._query_id = query_id
        self._headers = {"User-Agent": user_agent}
        self._timeout = timeout
        self._poll_interval = poll_interval
        self._max_poll_attempts = max_poll_attempts

    async def fetch_csv(
        self,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> str:
        """Generate the configured query and return its CSV statement."""
        if (from_date is None) != (to_date is None):
            raise ValueError("from_date and to_date must be provided together")
        if from_date and to_date:
            if from_date > to_date:
                raise ValueError("from_date cannot be after to_date")
            if (to_date - from_date).days >= 365:
                raise ValueError("IBKR Flex date ranges cannot exceed 365 days")

        send_params = {
            "t": self._token,
            "q": self._query_id,
            "v": "3",
        }
        if from_date and to_date:
            send_params["fd"] = from_date.strftime("%Y%m%d")
            send_params["td"] = to_date.strftime("%Y%m%d")

        async with httpx.AsyncClient(
            base_url=_BASE_URL,
            headers=self._headers,
            timeout=self._timeout,
            follow_redirects=True,
        ) as http:
            send_body = await self._get(http, "/SendRequest", send_params)
            parsed = _parse_service_response(send_body)
            if parsed is None:
                raise IbkrFlexError("Unexpected response while generating the report")

            status, reference_code, error_detail = parsed
            if status != "Success" or not reference_code:
                code, detail = self._split_error(error_detail)
                raise IbkrFlexError(detail or "The report could not be generated", code)

            get_params = {
                "t": self._token,
                "q": reference_code,
                "v": "3",
            }
            for attempt in range(self._max_poll_attempts):
                statement = await self._get(http, "/GetStatement", get_params)
                parsed = _parse_service_response(statement)
                if parsed is None:
                    return statement.lstrip("\ufeff")

                status, _, error_detail = parsed
                code, detail = self._split_error(error_detail)
                if status == "Fail" and code in _RETRYABLE_STATEMENT_CODES:
                    if attempt + 1 == self._max_poll_attempts:
                        break
                    await asyncio.sleep(self._poll_interval)
                    continue
                if status == "Fail":
                    raise IbkrFlexError(detail or "The report could not be retrieved", code)

                # A successful XML service envelope is not the configured CSV report.
                raise IbkrFlexError("Unexpected XML response while retrieving the report")

        raise IbkrFlexError(
            "Timed out waiting for the report to finish generating",
            "1019",
        )

    async def _get(
        self,
        http: httpx.AsyncClient,
        path: str,
        params: dict[str, str],
    ) -> str:
        try:
            response = await http.get(path, params=params)
        except httpx.RequestError as exc:
            # Do not stringify the exception: its request URL contains the Flex token.
            raise IbkrFlexError(
                f"Network request failed ({type(exc).__name__})"
            ) from exc

        if response.status_code >= 400:
            raise IbkrFlexError(f"IBKR returned HTTP {response.status_code}")
        return response.text

    @staticmethod
    def _split_error(error_detail: str | None) -> tuple[str | None, str | None]:
        if not error_detail:
            return None, None
        code, _, detail = error_detail.partition("|")
        return code or None, detail or None
