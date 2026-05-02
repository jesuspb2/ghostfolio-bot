"""Parser for DEGIRO Account.csv exports.

CSV header: Datum,Tijd,Valutadatum,Product,ISIN,Omschrijving,FX,Mutatie,,Saldo,,Order Id
Positional columns:
  0=date, 1=time, 2=currencyDate, 3=product, 4=isin, 5=description,
  6=fx, 7=currency, 8=amount, 9=balCurrency, 10=balAmount, 11=orderId

Note: Uses csv.reader for positional access to handle the two duplicate empty column headers.

Record pairing logic:
  - Buy/sell records are paired with fee records that share the same orderId.
    Fee may appear BEFORE or AFTER the buy/sell (both orderings seen in real exports).
  - Dividend records are paired with tax records by same ISIN + date (no orderId).
    Tax may appear BEFORE or AFTER the dividend record.

Reference: examples/Export-To-Ghostfolio/src/converters/degiroConverterV3.ts
Sample:    examples/Export-To-Ghostfolio/samples/degiro-export.csv
"""

from __future__ import annotations

import csv
import difflib
import io
import logging
import re
import unicodedata
from datetime import datetime
from typing import Any, ClassVar

from bot.parsers.base import BrokerParser, GhostfolioActivity, register_parser

logger = logging.getLogger(__name__)

# Positional column indices (Account.csv always has exactly 12 columns)
_DATE = 0
_TIME = 1
_PRODUCT = 3
_ISIN = 4
_DESCRIPTION = 5
_CURRENCY = 7
_AMOUNT = 8
_ORDER_ID = 11

_IGNORED_KEYWORDS = [
    "ideal", "flatex", "cash sweep", "withdrawal",
    "productwijziging", "währungswechsel", "trasferisci",
    "deposito", "credito", "credit", "prelievo",
    "creditering", "debitering", "rente", "interesse",
    "verrekening promotie", "operation de change",
    "versement de fonds", "débit", "debit",
    "depósito", "ingreso", "retirada",
    "levantamento de divisa", "dito de divisa",
    "fonds monétaires",   # FR: money market fund rebalancing
    "geldmarktfonds",     # DE/NL: money market fund rebalancing
    "money market",       # EN
    # Spanish: interest charges on negative balance, transfer fees
    "interés", "comisión por transferencia",
    # Spanish: corporate actions that are internal accounting entries (not real trades)
    "emisión de derechos",   # rights issuance at price 0
    "cambio de isin",        # ISIN rename — would double-count positions
    "conversión fondos",     # money market rebalancing (belt-and-suspenders)
]

_PLATFORM_FEE_KEYWORDS = [
    "aansluitingskosten", "connection fee", "costi di connessione",
    "verbindungskosten", "custo de conectividade", "frais de connexion",
    "juros", "corporate action",
    # Spanish: market connectivity fee
    "comisión de conectividad",
]

# Matches both buy/sell transaction fees AND dividend withholding taxes
_FEE_LIKE_KEYWORDS = [
    "en/of", "and/or", "und/oder", "e/o", "y/o", "adr/gdr",
    "ritenuta", "belasting", "daň z dividendy",
    "taxe sur les", "impôts sur", "comissões de transação",
    "courtage et/ou",
    # Spanish: dividend withholding tax
    "retención",
]

# Pre-compiled regexes for keyword matching (faster than any(k in t) for 10+ keywords)
_IGNORED_RE = re.compile("|".join(re.escape(k) for k in _IGNORED_KEYWORDS))
_PLATFORM_FEE_RE = re.compile("|".join(re.escape(k) for k in _PLATFORM_FEE_KEYWORDS))
_FEE_LIKE_RE = re.compile("|".join(re.escape(k) for k in _FEE_LIKE_KEYWORDS))

# Pre-compiled regexes for _sanitise_symbol (avoid recompiling on every call)
_RE_NON_ALNUM = re.compile(r"[^A-Za-z0-9\s-]")
_RE_SPACES = re.compile(r"\s+")
_RE_MULTI_DASH = re.compile(r"-{2,}")


def _sanitise_symbol(description: str) -> str:
    """Create a short Ghostfolio-safe symbol from a free-text description.

    Ghostfolio's import endpoint rejects long strings and non-ASCII chars as
    MANUAL symbols. We normalise to ASCII, keep only alphanumerics and hyphens,
    collapse runs, and cap at 32 chars.
    """
    s = unicodedata.normalize("NFKD", description)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = _RE_NON_ALNUM.sub("", s)
    s = _RE_SPACES.sub("-", s.strip())
    s = _RE_MULTI_DASH.sub("-", s)
    return s[:32].rstrip("-") or "FEE"


def _normalise_currency(raw: str) -> str:
    """Return a valid ISO4217 code or empty string if the value is unusable."""
    c = raw.strip().upper()
    if c == "GBX":
        return "GBp"
    # Accept only 3-letter codes (ISO4217); reject empty or garbage values
    if len(c) == 3 and c.isalpha():
        return c
    if c == "GBP":
        return "GBP"
    return ""


def _is_ignored(desc: str) -> bool:
    return bool(_IGNORED_RE.search(desc.lower()))


def _is_platform_fee(desc: str) -> bool:
    return bool(_PLATFORM_FEE_RE.search(desc.lower()))


def _is_interest(desc: str) -> bool:
    return "degiro courtesy" in desc.lower()


def _is_fee_like(desc: str) -> bool:
    return bool(_FEE_LIKE_RE.search(desc.lower()))


def _is_buy_sell(desc: str) -> bool:
    d = desc.lower()
    return "@" in d or "zu je" in d


def _is_stock_dividend(desc: str) -> bool:
    return "stock dividend" in desc.lower()


def _is_dividend(desc: str) -> bool:
    d = desc.lower()
    return "dividend" in d or "capital return" in d


def _parse_amount(raw: str) -> float:
    """Parse European-style decimal: '33,90' → 33.9, '1.234,56' → 1234.56."""
    s = raw.strip().strip('"')
    if not s:
        return 0.0
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    elif "," in s and "." in s:
        # thousands dot + decimal comma (e.g. "1.234,56")
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def _parse_dt(date_str: str, time_str: str) -> datetime | None:
    d = date_str.strip()
    t = (time_str or "00:00").strip() or "00:00"
    if not d:
        return None
    try:
        return datetime.strptime(f"{d} {t}:00", "%d-%m-%Y %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(d, "%d-%m-%Y")
        except ValueError:
            return None


@register_parser
class DegiroParser(BrokerParser):
    name = "DEGIRO"
    slug = "degiro"
    HEADER_SIGNATURE = (
        "Datum,Tijd,Valutadatum,Product,ISIN,Omschrijving,FX,Mutatie,,Saldo,,Order Id"
    )
    DELIMITER = ","

    _ALL_SIGNATURES: ClassVar[list[str]] = [
        "Datum,Tijd,Valutadatum,Product,ISIN,Omschrijving,FX,Mutatie,,Saldo,,Order Id",  # NL
        "Date,Time,Value date,Product,ISIN,Description,Type,Variation,,Balance,,Order ID",  # EN
        "Fecha,Hora,Fecha valor,Producto,ISIN,Descripción,Tipo,Variación,,Saldo,,ID Orden",  # ES
    ]

    @classmethod
    def detect(cls, header_line: str) -> float:
        h = header_line.strip()
        return max(
            difflib.SequenceMatcher(None, h, sig).ratio()
            for sig in cls._ALL_SIGNATURES
        )

    def parse(self, csv_content: str) -> list[GhostfolioActivity]:
        content = csv_content.lstrip("\ufeff")
        records = self._read_records(content)
        return self._process(records)

    def _read_records(self, content: str) -> list[dict[str, Any]]:
        """Use csv.reader for positional access (the two unnamed columns break DictReader)."""
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        if not rows:
            return []
        result = []
        for row in rows[1:]:  # skip header
            while len(row) < 12:
                row.append("")
            result.append({
                "date": row[_DATE].strip(),
                "time": row[_TIME].strip(),
                "product": row[_PRODUCT].strip(),
                "isin": row[_ISIN].strip(),
                "description": row[_DESCRIPTION].strip(),
                "currency": row[_CURRENCY].strip(),
                "amount": row[_AMOUNT].strip(),
                "order_id": row[_ORDER_ID].strip(),
            })
        return result

    def _process(self, records: list[dict[str, Any]]) -> list[GhostfolioActivity]:
        activities: list[GhostfolioActivity] = []
        consumed: set[int] = set()

        # Pre-build indexes so partner lookups are O(1) instead of O(n).
        # oid_index: order_id → [indices of records with that order_id]
        # div_index: (isin, date) → [indices of records with no order_id]
        oid_index: dict[str, list[int]] = {}
        div_index: dict[tuple[str, str], list[int]] = {}
        for i, rec in enumerate(records):
            if rec["order_id"]:
                oid_index.setdefault(rec["order_id"], []).append(i)
            elif rec["isin"] and rec["date"]:
                div_index.setdefault((rec["isin"], rec["date"]), []).append(i)

        def _find_by_oid(
            order_id: str, exclude: int, predicate: Any
        ) -> tuple[int, dict[str, Any]] | None:
            for i in oid_index.get(order_id, []):
                if i != exclude and i not in consumed and predicate(records[i]):
                    return i, records[i]
            return None

        def _find_by_div_key(
            isin: str, date: str, exclude: int, predicate: Any
        ) -> tuple[int, dict[str, Any]] | None:
            for i in div_index.get((isin, date), []):
                if i != exclude and i not in consumed and predicate(records[i]):
                    return i, records[i]
            return None

        for idx, rec in enumerate(records):
            if idx in consumed:
                continue

            desc = rec["description"]
            if not desc:
                continue
            if not rec["date"] and not rec["product"] and not rec["isin"]:
                continue
            if _is_ignored(desc):
                continue

            if _is_platform_fee(desc):
                a = self._make_platform_fee(rec)
                if a:
                    activities.append(a)
                continue

            if _is_interest(desc):
                a = self._make_interest(rec)
                if a:
                    activities.append(a)
                continue

            # Fee-like + dividend keyword → dividend withholding tax.
            # May appear before or after its dividend record (look up by isin+date).
            if _is_fee_like(desc) and _is_dividend(desc):
                if rec["order_id"]:
                    # Has orderId → unusual, skip rather than misclassify
                    continue
                result = _find_by_div_key(
                    rec["isin"], rec["date"], idx,
                    lambda r: (
                        _is_dividend(r["description"])
                        and not _is_fee_like(r["description"])
                    ),
                )
                if result:
                    div_idx, div_rec = result
                    a = self._make_dividend(div_rec, rec)
                    if a:
                        activities.append(a)
                    consumed.add(idx)
                    consumed.add(div_idx)
                # No matching dividend found → skip (orphan or already consumed)
                continue

            # Pure transaction fee (no dividend keyword) with orderId → find buy/sell partner.
            if _is_fee_like(desc):
                order_id = rec["order_id"]
                if order_id:
                    result = _find_by_oid(
                        order_id, idx,
                        lambda r: _is_buy_sell(r["description"]),
                    )
                    if result:
                        buy_idx, buy_rec = result
                        a = self._make_buy_sell(buy_rec, rec)
                        if a:
                            activities.append(a)
                        consumed.add(idx)
                        consumed.add(buy_idx)
                # Skip regardless (orphan fee or consumed together above)
                continue

            # Buy or sell record (may already have had its fee processed above)
            if _is_buy_sell(desc) or _is_stock_dividend(desc):
                order_id = rec["order_id"]
                fee_rec = None
                if order_id:
                    result = _find_by_oid(
                        order_id, idx,
                        lambda r: _is_fee_like(r["description"]),
                    )
                    if result:
                        fee_idx, fee_rec = result
                        consumed.add(fee_idx)
                a = self._make_buy_sell(rec, fee_rec)
                if a:
                    activities.append(a)
                consumed.add(idx)
                continue

            # Dividend record → find withholding tax partner (same ISIN + date, no orderId)
            if _is_dividend(desc):
                result = _find_by_div_key(
                    rec["isin"], rec["date"], idx,
                    lambda r: _is_fee_like(r["description"]),
                )
                tax_rec = None
                if result:
                    tax_idx, tax_rec = result
                    consumed.add(tax_idx)
                a = self._make_dividend(rec, tax_rec)
                if a:
                    activities.append(a)
                consumed.add(idx)
                continue

            logger.debug("Unhandled DEGIRO record: %s", desc)

        logger.info("Parsed %d activities from DEGIRO (%d rows)", len(activities), len(records))
        return activities

    def _make_buy_sell(
        self, rec: dict[str, Any], fee_rec: dict[str, Any] | None
    ) -> GhostfolioActivity | None:
        desc = rec["description"]
        amount = _parse_amount(rec["amount"])

        m = re.search(r"(\d+[.,]?\d*)", desc)
        if not m:
            logger.warning("Cannot parse quantity from DEGIRO description: %s", desc)
            return None
        quantity = _parse_amount(m.group(1))
        if quantity <= 0:
            return None

        unit_price = round(abs(amount) / quantity, 3) if quantity else 0.0
        if unit_price == 0.0 and not _is_stock_dividend(desc):
            logger.warning("Skipping zero-price buy/sell (likely corporate action): %s", desc)
            return None
        order_type = "BUY" if amount < 0 or _is_stock_dividend(desc) else "SELL"
        fee = abs(_parse_amount(fee_rec["amount"])) if fee_rec else 0.0

        dt = _parse_dt(rec["date"], rec["time"])
        if not dt:
            return None

        order_id = rec.get("order_id", "")
        comment = (
            order_id or f"{order_type.capitalize()} {rec['isin']} @ {rec['date']}T{rec['time']}"
        )

        currency = _normalise_currency(rec["currency"])
        if not currency:
            logger.warning("Skipping buy/sell with empty currency: %s", desc)
            return None

        return GhostfolioActivity(
            currency=currency,
            data_source="YAHOO",
            date=dt,
            fee=fee,
            quantity=quantity,
            symbol=rec["isin"],
            type=order_type,
            unit_price=unit_price,
            comment=comment,
        )

    def _make_dividend(
        self, rec: dict[str, Any], tax_rec: dict[str, Any] | None
    ) -> GhostfolioActivity | None:
        dt = _parse_dt(rec["date"], rec["time"])
        if not dt:
            return None

        currency = _normalise_currency(rec["currency"])
        if not currency:
            logger.warning("Skipping dividend with empty currency: %s", rec["isin"])
            return None

        unit_price = abs(_parse_amount(rec["amount"]))
        fee = abs(_parse_amount(tax_rec["amount"])) if tax_rec else 0.0

        return GhostfolioActivity(
            currency=currency,
            data_source="YAHOO",
            date=dt,
            fee=fee,
            quantity=1,
            symbol=rec["isin"],
            type="DIVIDEND",
            unit_price=unit_price,
            comment=f"Dividend {rec['isin']} @ {rec['date']}T{rec['time']}",
        )

    def _make_platform_fee(self, rec: dict[str, Any]) -> GhostfolioActivity | None:
        dt = _parse_dt(rec["date"], rec["time"])
        if not dt:
            return None
        currency = _normalise_currency(rec["currency"])
        if not currency:
            logger.warning("Skipping platform fee with empty currency: %s", rec["description"])
            return None
        return GhostfolioActivity(
            currency=currency,
            data_source="MANUAL",
            date=dt,
            fee=0.0,
            quantity=1,
            symbol=_sanitise_symbol(rec["description"]),
            type="FEE",
            unit_price=abs(_parse_amount(rec["amount"])),
            comment=rec["description"],
        )

    def _make_interest(self, rec: dict[str, Any]) -> GhostfolioActivity | None:
        dt = _parse_dt(rec["date"], rec["time"])
        if not dt:
            return None
        currency = _normalise_currency(rec["currency"])
        if not currency:
            logger.warning("Skipping interest with empty currency: %s", rec["description"])
            return None
        return GhostfolioActivity(
            currency=currency,
            data_source="MANUAL",
            date=dt,
            fee=0.0,
            quantity=1,
            symbol=_sanitise_symbol(rec["description"]),
            type="INTEREST",
            unit_price=abs(_parse_amount(rec["amount"])),
            comment=rec["description"],
        )


