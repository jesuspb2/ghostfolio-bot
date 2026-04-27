"""Parser registry — import this package to trigger parser auto-registration."""

from bot.parsers.base import (
    BrokerParser,
    GhostfolioActivity,
    auto_detect_parser,
    get_all_parsers,
    get_parser,
    register_parser,
)
from bot.parsers.revolut_savings import (
    RevolutSavingsParser,
)

__all__ = [
    "BrokerParser",
    "GhostfolioActivity",
    "RevolutSavingsParser",
    "auto_detect_parser",
    "get_all_parsers",
    "get_parser",
    "register_parser",
]
