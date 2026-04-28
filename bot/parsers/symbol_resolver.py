"""ISIN → Yahoo Finance ticker resolution with in-memory + disk cache.

Used by the import handler to convert raw ISINs (as emitted by DEGIRO, IBKR,
etc.) into the ticker symbols that Ghostfolio's YAHOO data source accepts.

Crypto parsers (Revolut Crypto, Delta) already produce constructed symbols like
"BTC-EUR" which are valid Yahoo Finance pairs and are NOT ISINs, so they are
left untouched.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
_EQUITY_TYPES = {"EQUITY", "ETF", "MUTUALFUND"}

_memory_cache: dict[str, str] = {}   # isin -> resolved ticker
_cache_loaded = False

_CACHE_PATH = Path.home() / ".cache" / "ghostfolio-bot" / "isin_cache.json"


def is_isin(s: str) -> bool:
    """Return True if s looks like an ISIN (ISO 6166)."""
    return bool(_ISIN_RE.match(s))


# -- Cache helpers -------------------------------------------------------------

def _load_cache() -> None:
    global _cache_loaded
    if _cache_loaded:
        return
    _cache_loaded = True
    try:
        if _CACHE_PATH.exists():
            data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            _memory_cache.update(data)
            logger.debug("Loaded %d ISIN→ticker entries from disk cache", len(data))
    except Exception as exc:
        logger.warning("Could not load ISIN cache from disk: %s", exc)


def _persist_cache() -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(
            json.dumps(_memory_cache, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        logger.warning("Could not persist ISIN cache to disk: %s", exc)


# -- Yahoo Finance lookup (sync, run in executor) ------------------------------

def _yahoo_search(isin: str, currency: str | None) -> str | None:
    """Search Yahoo Finance for an ISIN and return the best ticker match."""
    try:
        import yfinance as yf  # type: ignore[import-untyped]
    except ImportError:
        logger.error("yfinance is not installed — cannot resolve ISINs")
        return None

    try:
        quotes = yf.Search(isin, max_results=10).quotes
    except Exception as exc:
        logger.warning("Yahoo Finance search error for %s: %s", isin, exc)
        return None
    if not quotes:
        logger.debug("No Yahoo Finance results for ISIN %s", isin)
        return None

    # Prefer equity-like types; fall back to all quotes if none match
    equity = [q for q in quotes if q.get("quoteType", "") in _EQUITY_TYPES]
    candidates = equity if equity else quotes

    # Filter out quotes where Yahoo returned the ISIN itself as the ticker
    # (e.g. "US91680M1071.SG" — ISIN + exchange suffix, meaning no real ticker found).
    real_candidates = [q for q in candidates if not q.get("symbol", "").startswith(isin)]
    if not real_candidates:
        logger.debug(
            "Yahoo Finance only returned ISIN-suffixed symbols for %s — treating as unresolved",
            isin,
        )
        return None
    candidates = real_candidates

    # Among candidates, prefer one matching the transaction currency
    if currency:
        norm = "GBp" if currency.upper() == "GBX" else currency
        for q in candidates:
            if q.get("currency", "").upper() == norm.upper():
                return str(q["symbol"])

    return str(candidates[0]["symbol"])


# -- Public async API ----------------------------------------------------------

async def resolve_isin(isin: str, currency: str | None = None) -> str | None:
    """Async: resolve an ISIN to a Yahoo Finance ticker.

    Returns the ticker string, or None if Yahoo Finance has no match.
    Results are cached in memory and on disk to avoid repeated API calls.
    """
    _load_cache()

    # Check cache (currency-specific first, then generic).
    # Reject stale entries where Yahoo returned an ISIN-derived symbol (e.g. US91680M1071.SG)
    # before the _yahoo_search filter was in place — those would crash Ghostfolio.
    cache_key = f"{isin}:{currency}" if currency else isin
    for key in (cache_key, isin):
        if key in _memory_cache:
            cached = _memory_cache[key]
            if cached and not cached.startswith(isin):
                return cached
            # Stale ISIN-derived entry — evict and re-resolve
            _memory_cache.pop(cache_key, None)
            _memory_cache.pop(isin, None)
            _persist_cache()
            logger.info("Evicted stale ISIN-derived cache entry %s → %s", isin, cached)
            break

    loop = asyncio.get_event_loop()
    ticker = await loop.run_in_executor(None, _yahoo_search, isin, currency)

    if ticker:
        _memory_cache[cache_key] = ticker
        _memory_cache[isin] = ticker  # generic fallback for future lookups
        logger.info("Resolved ISIN %s → %s (currency=%s)", isin, ticker, currency)

    return ticker


async def resolve_symbols(
    activities: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve ISIN symbols to Yahoo Finance tickers in a list of activity dicts.

    Only touches activities where dataSource="YAHOO" and symbol looks like an ISIN.
    Mutates the dicts in-place and returns (activities, unresolvable_isins).

    Deduplicates ISINs before querying so each unique ISIN is looked up once.
    """
    # Collect unique (isin, currency) pairs that need resolution
    to_resolve: dict[str, str | None] = {}  # isin -> currency (last seen wins)
    for a in activities:
        if a.get("dataSource") != "YAHOO":
            continue
        symbol = a.get("symbol", "")
        if is_isin(symbol):
            to_resolve[symbol] = a.get("currency")

    if not to_resolve:
        return activities, []

    logger.info("Resolving %d unique ISIN(s) via Yahoo Finance…", len(to_resolve))

    # Resolve all unique ISINs concurrently
    resolved_map: dict[str, str | None] = {}
    tasks = {isin: resolve_isin(isin, currency) for isin, currency in to_resolve.items()}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    for isin, result in zip(tasks.keys(), results, strict=False):
        if isinstance(result, Exception):
            logger.warning("Exception resolving %s: %s", isin, result)
            resolved_map[isin] = None
        else:
            resolved_map[isin] = result  # type: ignore[assignment]

    # Persist cache once after all ISINs resolved (avoids N disk writes)
    _persist_cache()

    # Apply resolved tickers back to activities
    unresolvable: list[str] = []
    for a in activities:
        if a.get("dataSource") != "YAHOO":
            continue
        symbol = a.get("symbol", "")
        if not is_isin(symbol):
            continue
        ticker = resolved_map.get(symbol)
        if ticker:
            a["symbol"] = ticker
        else:
            if symbol not in unresolvable:
                unresolvable.append(symbol)

    if unresolvable:
        logger.warning(
            "%d ISIN(s) could not be resolved — activities will be skipped by Ghostfolio: %s",
            len(unresolvable),
            unresolvable,
        )

    return activities, unresolvable
