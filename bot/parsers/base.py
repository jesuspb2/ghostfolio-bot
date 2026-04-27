"""Base class for broker CSV parsers and parser registry."""

from __future__ import annotations

import csv
import io
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class GhostfolioActivity:
    """A single activity ready for Ghostfolio import."""

    currency: str
    data_source: str  # YAHOO | MANUAL | COINGECKO
    date: datetime
    fee: float
    quantity: float
    symbol: str
    type: str  # BUY | SELL | DIVIDEND | ITEM | FEE
    unit_price: float
    comment: str = ""

    def to_dict(self, account_id: str) -> dict[str, Any]:
        """Serialize to the JSON format expected by Ghostfolio's import API."""
        return {
            "accountId": account_id,
            "comment": self.comment,
            "currency": self.currency,
            "dataSource": self.data_source,
            "date": self.date.strftime("%Y-%m-%dT00:00:00.000Z"),
            "fee": self.fee,
            "quantity": self.quantity,
            "symbol": self.symbol,
            "type": self.type,
            "unitPrice": self.unit_price,
        }


class BrokerParser(ABC):
    """Abstract base for broker CSV parsers."""

    name: str = "Unknown"
    slug: str = "unknown"

    @abstractmethod
    def parse(self, csv_content: str) -> list[GhostfolioActivity]:
        """Parse CSV content into a list of Ghostfolio activities."""
        ...

    def can_handle(self, csv_content: str) -> bool:
        """Auto-detect whether this parser can handle the given CSV."""
        return False

    def _read_csv(self, content: str) -> list[dict[str, str]]:
        """Helper: parse CSV string into list of row dicts, trying common delimiters."""
        for delimiter in [",", ";", "\t"]:
            try:
                reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
                rows = list(reader)
                if rows and reader.fieldnames and len(reader.fieldnames) > 1:
                    return rows
            except Exception:
                continue

        raise ValueError("Could not parse CSV with any common delimiter")


# -- Parser registry -----------------------------------------------------------

_PARSERS: dict[str, type[BrokerParser]] = {}


def register_parser(cls: type[BrokerParser]) -> type[BrokerParser]:
    """Class decorator to register a parser in the global registry."""
    _PARSERS[cls.slug] = cls
    return cls


def get_parser(slug: str) -> BrokerParser:
    """Get a parser instance by slug."""
    if slug not in _PARSERS:
        available = ", ".join(sorted(_PARSERS.keys()))
        raise ValueError(f"Unknown parser '{slug}'. Available: {available}")
    return _PARSERS[slug]()


def get_all_parsers() -> dict[str, type[BrokerParser]]:
    """Return all registered parsers."""
    return dict(_PARSERS)


def auto_detect_parser(csv_content: str) -> BrokerParser | None:
    """Try to auto-detect which parser can handle the CSV content."""
    for parser_cls in _PARSERS.values():
        parser = parser_cls()
        if parser.can_handle(csv_content):
            logger.info("Auto-detected parser: %s", parser.name)
            return parser
    return None
