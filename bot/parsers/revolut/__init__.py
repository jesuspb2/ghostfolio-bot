"""Revolut parsers — import this subpackage to register all three."""

from bot.parsers.revolut.crypto import RevolutCryptoParser
from bot.parsers.revolut.invest import RevolutInvestParser
from bot.parsers.revolut.savings import RevolutSavingsParser

__all__ = ["RevolutCryptoParser", "RevolutInvestParser", "RevolutSavingsParser"]
