"""Tests for Revolut Crypto parser."""

from bot.parsers.revolut.crypto import RevolutCryptoParser

SAMPLE_CSV = (
    "Symbol,Type,Quantity,Price,Value,Fees,Date\n"
    'BTC,Buy,0.00056077,"89,162.28 SEK",50.00 SEK,0.00 SEK,"May 5, 2020, 10:10:57 PM"\n'
    'BTC,Sell,0.00001082,"86,557.95 SEK",0.93 SEK,0.00 SEK,"Jun 2, 2020, 9:10:31 PM"\n'
    'BTC,Send,0.00056077,"137,211.36 SEK",76.94 SEK,0.00 SEK,"Nov 9, 2020, 3:50:00 AM"\n'
    'BTC,Receive,0.00056077,"137,211.36 SEK",76.94 SEK,0.00 SEK,"Nov 9, 2020, 3:50:00 AM"\n'
    'BTC,Buy,0.00027963,"€35,761.54",€10.00,€0.24,"Mar 8, 2022, 2:08:54 PM"\n'
    'ETH,Staking reward,0.00002061,,,,"Jan 15, 2025, 12:23:31 AM"\n'
    'ETH,Buy,0.00431791,"€2,315.93",€10.00,€0.14,"Feb 9, 2024, 9:46:02 AM"\n'
    'BTC,Buy,0.00009518,"$105,058.55",$10.00,$0.24,"May 9, 2025, 10:03:50 AM"\n'
)


def test_can_handle_revolut_crypto() -> None:
    parser = RevolutCryptoParser()
    assert parser.can_handle(SAMPLE_CSV) is True


def test_can_handle_rejects_unknown() -> None:
    parser = RevolutCryptoParser()
    assert parser.can_handle("Date,Ticker,Type\n2024-01-01,AAPL,BUY") is False


def test_skips_send_receive() -> None:
    parser = RevolutCryptoParser()
    activities = parser.parse(SAMPLE_CSV)
    # BTC-SEK: BUY + SELL survive, Send and Receive are skipped
    btc_sek = [a for a in activities if a.symbol == "BTC-SEK"]
    assert len(btc_sek) == 2
    assert {a.type for a in btc_sek} == {"BUY", "SELL"}


def test_buy_sek() -> None:
    parser = RevolutCryptoParser()
    activities = parser.parse(SAMPLE_CSV)
    btc_sek = [a for a in activities if a.symbol == "BTC-SEK" and a.type == "BUY"]
    assert len(btc_sek) == 1
    assert btc_sek[0].quantity == 0.00056077
    assert btc_sek[0].unit_price == 89162.28
    assert btc_sek[0].currency == "SEK"
    assert btc_sek[0].fee == 0.0
    assert btc_sek[0].data_source == "YAHOO"


def test_buy_eur() -> None:
    parser = RevolutCryptoParser()
    activities = parser.parse(SAMPLE_CSV)
    btc_eur = [a for a in activities if a.symbol == "BTC-EUR" and a.type == "BUY"]
    assert len(btc_eur) == 1
    assert btc_eur[0].unit_price == 35761.54
    assert btc_eur[0].currency == "EUR"
    assert btc_eur[0].fee == 0.24


def test_buy_usd() -> None:
    parser = RevolutCryptoParser()
    activities = parser.parse(SAMPLE_CSV)
    btc_usd = [a for a in activities if a.symbol == "BTC-USD"]
    assert len(btc_usd) == 1
    assert btc_usd[0].unit_price == 105058.55
    assert btc_usd[0].currency == "USD"
    assert btc_usd[0].fee == 0.24


def test_sell_activity() -> None:
    parser = RevolutCryptoParser()
    activities = parser.parse(SAMPLE_CSV)
    sells = [a for a in activities if a.type == "SELL"]
    assert len(sells) == 1
    assert sells[0].symbol == "BTC-SEK"
    assert sells[0].quantity == 0.00001082


def test_staking_reward_is_dividend() -> None:
    parser = RevolutCryptoParser()
    activities = parser.parse(SAMPLE_CSV)
    divs = [a for a in activities if a.type == "DIVIDEND"]
    assert len(divs) == 1
    assert divs[0].quantity == 1.0
    assert divs[0].unit_price == 0.0  # no price data in sample


def test_date_parsed() -> None:
    parser = RevolutCryptoParser()
    activities = parser.parse(SAMPLE_CSV)
    buy_sek = next(a for a in activities if a.symbol == "BTC-SEK" and a.type == "BUY")
    assert buy_sek.date.year == 2020
    assert buy_sek.date.month == 5
    assert buy_sek.date.day == 5
