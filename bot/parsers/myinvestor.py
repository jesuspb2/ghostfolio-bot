"""MyInvestor (Spain) XLS parser — fondos de inversión transactions.

MyInvestor exports transaction history as an HTML file disguised as .xls.
The relevant file is the "fondos" (funds) export, not the portfolio summary.

Column layout (positional, pandas MultiIndex header):
  0  Fecha Operación
  1  Fecha Liquidación
  2  Operación ID
  3  Mercado
  4  Tipo Operación  (SUSCRIPCION | REEMBOLSO | SUSCR.POR TRASPASO I | REEMB.POR TRASPASO I)
  5  ISIN
  6  Valor (fund name)
  7  Títulos/NOMINAL (quantity)
  8  Divisa (currency)
  9  Precio Neto (unit price)
  10 Importe neto (total amount)
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, timezone

import pandas as pd

from bot.parsers.base import BrokerParser, GhostfolioActivity, register_parser

logger = logging.getLogger(__name__)

_OP_MAP: dict[str, str] = {
    "SUSCRIPCION": "BUY",
    "SUSCR.POR TRASPASO I": "BUY",
    "REEMBOLSO": "SELL",
    "REEMB.POR TRASPASO I": "SELL",
}

# Distinctive byte sequences present in the fondos XLS (HTML, ISO-8859-1 encoded).
# "Títulos/NOMINAL" encodes 'í' as 0xED in ISO-8859-1.
_DETECT_KEYWORDS: list[bytes] = [b"Precio Neto", b"T\xedtulos/NOMINAL"]


@register_parser
class MyInvestorParser(BrokerParser):
    name = "MyInvestor"
    slug = "myinvestor"
    HEADER_SIGNATURE = ""  # binary parser — CSV detect not used

    @classmethod
    def detect(cls, header_line: str) -> float:
        return 0.0

    @classmethod
    def detect_binary(cls, file_bytes: bytes) -> float:
        matched = sum(1 for kw in _DETECT_KEYWORDS if kw in file_bytes)
        return matched / len(_DETECT_KEYWORDS)

    def parse(self, csv_content: str) -> list[GhostfolioActivity]:
        raise ValueError(
            "MyInvestor files are XLS, not CSV. Upload the .xls fondos file."
        )

    def parse_binary(self, file_bytes: bytes) -> list[GhostfolioActivity]:
        try:
            tables = pd.read_html(io.BytesIO(file_bytes), encoding="iso-8859-1")
        except Exception as e:
            raise ValueError(f"Could not parse MyInvestor XLS: {e}") from e

        # The fondos table has ≥10 columns; salida.xls only has 8-9.
        df: pd.DataFrame | None = None
        for t in tables:
            if t.shape[1] >= 10:
                df = t
                break

        if df is None:
            raise ValueError(
                "No transaction table found in this XLS. "
                "Please upload the fondos (transactions) export, not the portfolio summary."
            )

        activities: list[GhostfolioActivity] = []

        for _, row in df.iterrows():
            try:
                date_str = str(row.iloc[0]).strip()
                op_type_raw = str(row.iloc[4]).strip().upper()
                isin = str(row.iloc[5]).strip()
                quantity_raw = row.iloc[7]
                currency = str(row.iloc[8]).strip().upper()
                price_raw = row.iloc[9]

                # Skip rows that aren't actual transactions (headers, totals, NaN rows).
                if (
                    pd.isna(row.iloc[5])
                    or len(isin) != 12
                    or not isin[:2].isalpha()
                    or not isin[2:].isalnum()
                ):
                    continue

                op_type = _OP_MAP.get(op_type_raw)
                if not op_type:
                    logger.debug("MyInvestor: skipping unknown operation '%s'", op_type_raw)
                    continue

                try:
                    date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                except ValueError:
                    logger.warning("MyInvestor: skipping row with bad date '%s'", date_str)
                    continue

                quantity = float(quantity_raw)
                unit_price = float(price_raw)

                if quantity <= 0 or unit_price <= 0:
                    continue

                activities.append(
                    GhostfolioActivity(
                        currency=currency,
                        data_source="YAHOO",
                        date=date,
                        fee=0.0,
                        quantity=quantity,
                        symbol=isin,
                        type=op_type,
                        unit_price=unit_price,
                    )
                )
            except Exception:
                logger.debug("MyInvestor: skipping unparseable row", exc_info=True)
                continue

        logger.info("MyInvestor: parsed %d activities", len(activities))
        return activities
