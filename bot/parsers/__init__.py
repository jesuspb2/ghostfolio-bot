"""Parser registry — import this package to trigger parser auto-registration."""

from bot.parsers.base import (
    BrokerParser,
    GhostfolioActivity,
    auto_detect_parser,
    get_all_parsers,
    get_parser,
    register_parser,
)
from bot.parsers.delta import DeltaParser
from bot.parsers.ibkr import IbkrDividendsParser, IbkrTradesParser
from bot.parsers.revolut import RevolutCryptoParser, RevolutInvestParser, RevolutSavingsParser

__all__ = [
    "BrokerParser",
    "DeltaParser",
    "GhostfolioActivity",
    "IbkrDividendsParser",
    "IbkrTradesParser",
    "RevolutCryptoParser",
    "RevolutInvestParser",
    "RevolutSavingsParser",
    "auto_detect_parser",
    "get_all_parsers",
    "get_parser",
    "register_parser",
]
