"""Base class for broker CSV parsers and parser registry."""

from __future__ import annotations

import csv
import difflib
import io
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_DETECT_THRESHOLD = 0.6


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
            "date": self.date.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "fee": self.fee,
            "quantity": self.quantity,
            "symbol": self.symbol,
            "type": self.type,
            "unitPrice": self.unit_price,
            "updateAccountBalance": False,
        }


class BrokerParser(ABC):
    """Abstract base for broker CSV parsers."""

    name: str = "Unknown"
    slug: str = "unknown"
    HEADER_SIGNATURE: str = ""
    DELIMITER: str = ","

    def __init__(self) -> None:
        self.warnings: list[str] = []

    @classmethod
    def detect(cls, header_line: str) -> float:
        """Return similarity score 0.0-1.0 against HEADER_SIGNATURE."""
        if not cls.HEADER_SIGNATURE:
            return 0.0
        return difflib.SequenceMatcher(
            None, header_line.strip(), cls.HEADER_SIGNATURE
        ).ratio()

    @classmethod
    def detect_binary(cls, file_bytes: bytes) -> float:
        """Return similarity score 0.0-1.0 for binary (e.g. XLS) file detection."""
        return 0.0

    def can_handle(self, csv_content: str) -> bool:
        """Return True if this parser can handle the given CSV content."""
        header = _extract_header(csv_content)
        return type(self).detect(header) >= _DETECT_THRESHOLD

    @abstractmethod
    def parse(self, csv_content: str) -> list[GhostfolioActivity]:
        """Parse CSV content into a list of Ghostfolio activities."""
        ...

    def parse_binary(self, file_bytes: bytes) -> list[GhostfolioActivity]:
        """Parse binary file bytes (e.g. XLS). Override for binary-format parsers."""
        raise NotImplementedError(f"{type(self).__name__} does not support binary files")

    def _read_csv(self, content: str) -> list[dict[str, str]]:
        """Helper: parse CSV string into list of row dicts, trying common delimiters."""
        delimiters = [self.DELIMITER] if self.DELIMITER else [",", ";", "\t"]
        for delimiter in delimiters:
            try:
                reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
                rows = list(reader)
                if rows and reader.fieldnames and len(reader.fieldnames) > 1:
                    return rows
            except Exception:
                continue

        # Fallback: try all common delimiters
        for delimiter in [",", ";", "\t"]:
            try:
                reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)
                rows = list(reader)
                if rows and reader.fieldnames and len(reader.fieldnames) > 1:
                    return rows
            except Exception:
                continue

        raise ValueError("Could not parse CSV with any common delimiter")


def _extract_header(csv_content: str) -> str:
    """Return the first line that looks like a CSV header (has comma or semicolon)."""
    for line in csv_content.splitlines():
        stripped = line.strip()
        if stripped and ("," in stripped or ";" in stripped):
            return stripped
    return ""


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


def auto_detect_parser_binary(file_bytes: bytes) -> BrokerParser | None:
    """Auto-detect the best parser for a binary file (e.g. XLS) using detect_binary()."""
    best_score = 0.0
    best_cls: type[BrokerParser] | None = None

    for parser_cls in _PARSERS.values():
        score = parser_cls.detect_binary(file_bytes)
        if score > best_score:
            best_score = score
            best_cls = parser_cls

    if best_cls is not None and best_score >= _DETECT_THRESHOLD:
        logger.info(
            "Auto-detected binary parser '%s' (score=%.2f)", best_cls.name, best_score
        )
        return best_cls()

    return None


def auto_detect_parser(csv_content: str) -> BrokerParser | None:
    """Auto-detect the best parser for the given CSV using header similarity scoring."""
    header = _extract_header(csv_content)
    if not header:
        return None

    best_score = 0.0
    best_cls: type[BrokerParser] | None = None

    for parser_cls in _PARSERS.values():
        score = parser_cls.detect(header)
        if score > best_score:
            best_score = score
            best_cls = parser_cls

    if best_cls is not None and best_score >= _DETECT_THRESHOLD:
        logger.info(
            "Auto-detected parser '%s' (score=%.2f)", best_cls.name, best_score
        )
        return best_cls()

    return None
