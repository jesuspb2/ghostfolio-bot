"""Prepare an IBKR Flex trades report for an idempotent Ghostfolio import."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from bot.ghostfolio_client import (
    GhostfolioClient,
    build_manual_symbol_map,
    deduplicate_activities,
    resolve_manual_symbols,
)
from bot.parsers.ibkr.trade_confirmations import IbkrTradeConfirmationsParser
from bot.parsers.ibkr.trades import IbkrTradesParser
from bot.parsers.symbol_resolver import resolve_symbols


class IbkrSyncError(Exception):
    """Raised when the Flex report cannot be prepared for Ghostfolio."""


@dataclass(slots=True)
class IbkrSyncPreview:
    """Read-only preview of the activities a sync would import."""

    report_activities: int
    historical_activities: int
    intraday_activities: int
    existing_activities: int
    duplicates_skipped: int
    unresolved_activities: int
    invalid_currency_activities: int
    validation_errors: list[str]
    to_import: list[dict[str, Any]]


def select_ghostfolio_account(
    accounts: list[dict[str, Any]],
    *,
    account_id: str | None,
    account_name: str,
) -> tuple[str, str]:
    """Select the IBKR destination by explicit UUID or exact account name."""
    if account_id:
        matches = [
            account
            for account in accounts
            if (account.get("id") or account.get("accountId")) == account_id
        ]
        description = f"ID {account_id}"
    else:
        normalized_name = account_name.strip().casefold()
        matches = [
            account
            for account in accounts
            if str(account.get("name", "")).strip().casefold() == normalized_name
        ]
        description = f'name "{account_name}"'

    if not matches:
        raise IbkrSyncError(f"Ghostfolio account with {description} was not found.")
    if len(matches) > 1:
        raise IbkrSyncError(
            f"Multiple Ghostfolio accounts match {description}. "
            "Set IBKR_GHOSTFOLIO_ACCOUNT_ID explicitly."
        )

    selected = matches[0]
    selected_id = str(selected.get("id") or selected.get("accountId") or "")
    if not selected_id:
        raise IbkrSyncError(f"Ghostfolio account with {description} has no ID.")
    return selected_id, str(selected.get("name") or selected_id)


async def prepare_ibkr_sync(
    csv_content: str,
    account_id: str,
    ghostfolio: GhostfolioClient,
    trade_confirmation_content: str | None = None,
) -> IbkrSyncPreview:
    """Parse, combine, resolve and validate IBKR historical and intraday trades."""
    parser = IbkrTradesParser()
    if not parser.can_handle(csv_content):
        raise IbkrSyncError(
            "The Flex Query is not returning the expected IBKR Trades CSV format. "
            "Check the output format and selected columns."
        )

    historical = parser.parse(csv_content)
    intraday = []
    if trade_confirmation_content:
        confirmation_parser = IbkrTradeConfirmationsParser()
        if not confirmation_parser.can_handle(trade_confirmation_content):
            raise IbkrSyncError(
                "The Trade Confirmation Flex Query is not returning the expected "
                "IBKR XML format. Check its output format and selected columns."
            )
        intraday = confirmation_parser.parse(trade_confirmation_content)

    parsed = historical + intraday
    activity_dicts = [activity.to_dict(account_id) for activity in parsed]
    activity_dicts, unresolved_isins = await resolve_symbols(activity_dicts)

    unresolved_set = set(unresolved_isins)
    unresolved_count = sum(
        1 for activity in activity_dicts if activity.get("symbol") in unresolved_set
    )
    activity_dicts = [
        activity
        for activity in activity_dicts
        if activity.get("symbol") not in unresolved_set
    ]

    valid_currency = re.compile(r"^[A-Za-z]{3}$")
    invalid_currency_count = sum(
        1
        for activity in activity_dicts
        if not valid_currency.fullmatch(str(activity.get("currency", "")))
    )
    activity_dicts = [
        activity
        for activity in activity_dicts
        if valid_currency.fullmatch(str(activity.get("currency", "")))
    ]

    existing = await ghostfolio.get_orders(account_id=account_id)
    existing_list = (
        existing.get("activities", []) if isinstance(existing, dict) else existing or []
    )

    symbol_map = build_manual_symbol_map(existing_list)
    resolve_manual_symbols(activity_dicts, symbol_map)
    to_import = deduplicate_activities(activity_dicts, existing_list)
    duplicates_skipped = len(activity_dicts) - len(to_import)
    to_import.sort(key=lambda activity: str(activity.get("date", "")))

    validation_errors: list[str] = []
    if to_import:
        validation_errors = await ghostfolio.validate_activities(to_import)

    return IbkrSyncPreview(
        report_activities=len(parsed),
        historical_activities=len(historical),
        intraday_activities=len(intraday),
        existing_activities=len(existing_list),
        duplicates_skipped=duplicates_skipped,
        unresolved_activities=unresolved_count,
        invalid_currency_activities=invalid_currency_count,
        validation_errors=validation_errors,
        to_import=to_import,
    )
