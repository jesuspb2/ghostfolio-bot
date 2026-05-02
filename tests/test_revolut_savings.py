"""Tests for Revolut Savings parser — real CSV format."""

from bot.parsers.revolut.savings import RevolutSavingsParser

SAMPLE_CSV = """\
Date,Description,"Value, EUR",Price per share,Quantity of shares
"Apr 27, 2026, 12:52:19 PM",BUY EUR Class R IE000AZVL3K0,"3,200",1.00,"3,200"
"Apr 27, 2026, 4:28:54 AM",Service Fee Charged EUR Class IE000AZVL3K0,-0.5142,,
"Apr 27, 2026, 4:28:54 AM",Interest PAID EUR Class R IE000AZVL3K0,1.2242,,
"Apr 7, 2026, 12:30:20 PM",BUY EUR Class R IE000AZVL3K0,21.35,1.00,21.35
"Apr 6, 2026, 12:30:20 PM",Interest Reinvested Class R EUR IE000AZVL3K0,-21.35,,
"Apr 6, 2026, 4:12:29 AM",Service Fee Charged EUR Class IE000AZVL3K0,-0.5139,,
"Apr 6, 2026, 4:12:29 AM",Interest PAID EUR Class R IE000AZVL3K0,1.2039,,
"""


def test_can_handle_revolut_savings() -> None:
    parser = RevolutSavingsParser()
    assert parser.can_handle(SAMPLE_CSV) is True


def test_can_handle_rejects_unknown() -> None:
    parser = RevolutSavingsParser()
    assert parser.can_handle("Ticker,Date,Amount\nAAPL,2024-01-01,100") is False


def test_buy_activity() -> None:
    parser = RevolutSavingsParser()
    activities = parser.parse(SAMPLE_CSV)
    buys = [a for a in activities if a.type == "BUY"]

    assert len(buys) == 2
    big_buy = next(a for a in buys if a.quantity == 3200.0)
    assert big_buy.quantity == 3200.0
    assert big_buy.unit_price == 1.0
    assert big_buy.symbol == "IE000AZVL3K0"
    assert big_buy.currency == "EUR"
    assert big_buy.data_source == "YAHOO"


def test_interest_maps_to_dividend() -> None:
    parser = RevolutSavingsParser()
    activities = parser.parse(SAMPLE_CSV)
    dividends = [a for a in activities if a.type == "DIVIDEND"]

    assert len(dividends) == 2
    assert dividends[0].quantity == 1.0
    assert dividends[0].unit_price == 1.2242
    assert dividends[0].symbol == "IE000AZVL3K0"
    assert dividends[0].currency == "EUR"


def test_service_fee_activity() -> None:
    parser = RevolutSavingsParser()
    activities = parser.parse(SAMPLE_CSV)
    fees = [a for a in activities if a.type == "FEE"]

    assert len(fees) == 2
    assert fees[0].quantity == 1.0
    assert fees[0].unit_price == 0.5142  # abs value
    assert fees[0].symbol == "IE000AZVL3K0"


def test_interest_reinvested_is_skipped() -> None:
    parser = RevolutSavingsParser()
    activities = parser.parse(SAMPLE_CSV)
    comments = [a.comment for a in activities]
    assert not any("Interest Reinvested" in c for c in comments)


def test_total_activity_count() -> None:
    # 2 BUY + 2 DIVIDEND + 2 FEE = 6 (Interest Reinvested skipped)
    parser = RevolutSavingsParser()
    activities = parser.parse(SAMPLE_CSV)
    assert len(activities) == 6


def test_large_number_parsed_correctly() -> None:
    # "3,200" must parse as 3200.0, not 3.2
    parser = RevolutSavingsParser()
    activities = parser.parse(SAMPLE_CSV)
    buys = [a for a in activities if a.type == "BUY"]
    quantities = {a.quantity for a in buys}
    assert 3200.0 in quantities


def test_date_parsed() -> None:
    parser = RevolutSavingsParser()
    activities = parser.parse(SAMPLE_CSV)
    buy = next(a for a in activities if a.type == "BUY" and a.quantity == 3200.0)
    assert buy.date.year == 2026
    assert buy.date.month == 4
    assert buy.date.day == 27
    assert buy.date.hour == 12


def test_symbol_is_isin() -> None:
    parser = RevolutSavingsParser()
    activities = parser.parse(SAMPLE_CSV)
    for a in activities:
        assert a.symbol == "IE000AZVL3K0"


def test_data_source_is_yahoo() -> None:
    parser = RevolutSavingsParser()
    activities = parser.parse(SAMPLE_CSV)
    for a in activities:
        assert a.data_source == "YAHOO"
