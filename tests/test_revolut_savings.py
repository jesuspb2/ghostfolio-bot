"""Tests for Revolut Savings parser."""

from bot.parsers.revolut_savings import RevolutSavingsParser

SAMPLE_CSV = """Type,Product,Started Date,Completed Date,Description,Amount,Currency,State
INTEREST,Flexible EUR,2024-06-01 10:00:00,2024-06-01 10:00:00,Interest payment,2.50,EUR,COMPLETED
INTEREST,Flexible EUR,2024-07-01 10:00:00,2024-07-01 10:00:00,Interest payment,2.75,EUR,COMPLETED
TOPUP,Flexible EUR,2024-05-15 09:00:00,2024-05-15 09:00:00,Top-up,500.00,EUR,COMPLETED
INTEREST,Flexible EUR,2024-08-01 10:00:00,2024-08-01 10:00:00,Interest payment,3.10,EUR,PENDING
"""


def test_can_handle_revolut_savings() -> None:
    parser = RevolutSavingsParser()
    assert parser.can_handle(SAMPLE_CSV) is True


def test_can_handle_rejects_unknown() -> None:
    parser = RevolutSavingsParser()
    assert parser.can_handle("Ticker,Date,Amount\nAAPL,2024-01-01,100") is False


def test_parse_interest_activities() -> None:
    parser = RevolutSavingsParser()
    activities = parser.parse(SAMPLE_CSV)

    # Only COMPLETED INTEREST rows (2 of them) — TOPUP and PENDING are excluded
    assert len(activities) == 2
    assert activities[0].type == "DIVIDEND"
    assert activities[0].unit_price == 2.50
    assert activities[0].currency == "EUR"
    assert activities[1].unit_price == 2.75


def test_parse_skips_pending() -> None:
    parser = RevolutSavingsParser()
    activities = parser.parse(SAMPLE_CSV)

    prices = [a.unit_price for a in activities]
    assert 3.10 not in prices


def test_parse_skips_topup() -> None:
    parser = RevolutSavingsParser()
    activities = parser.parse(SAMPLE_CSV)

    for a in activities:
        assert "TOPUP" not in a.comment


def test_symbol_format() -> None:
    parser = RevolutSavingsParser()
    activities = parser.parse(SAMPLE_CSV)

    assert activities[0].symbol.startswith("REVOLUT-")
    assert "EUR" in activities[0].symbol
    assert activities[0].data_source == "MANUAL"


def test_data_source_is_manual() -> None:
    parser = RevolutSavingsParser()
    activities = parser.parse(SAMPLE_CSV)

    for a in activities:
        assert a.data_source == "MANUAL"


def test_interest_maps_to_dividend() -> None:
    parser = RevolutSavingsParser()
    activities = parser.parse(SAMPLE_CSV)

    for a in activities:
        assert a.type == "DIVIDEND"
