"""Tests for Revolut Invest (stocks) parser."""

from bot.parsers.revolut.invest import RevolutInvestParser

SAMPLE_CSV = (
    "Date,Ticker,Type,Quantity,Price per share,Total Amount,Currency,FX Rate\n"
    "2019-12-02T08:23:08.459586Z,,CASH WITHDRAWAL,,,-$30.93,USD,1.1019\n"
    "2019-11-15T23:15:55.878985Z,,CASH TOP-UP,,,$5.22,USD,1.1055\n"
    "2023-09-22T13:30:10.514Z,O,BUY - MARKET,1.63453043,$52.07,$85.11,USD,1.0665\n"
    "2023-07-14T13:30:00.797Z,MA,SELL - MARKET,0.1998348,$402.13,$80.34,USD,1.1241\n"
    "2019-12-13T08:40:00.835101Z,MSFT,DIVIDEND,,,$0.08,USD,1.1179\n"
    '2025-09-08T07:29:03.333Z,MSFT,BUY - MARKET,"0,76672417",€26.09,€20,EUR,1\n'
    "2021-09-01T07:40:54.539038Z,,CUSTODY FEE,,,-$0.01,USD,1.18\n"
    "2023-08-06T09:06:58.899860Z,WBD,TRANSFER FROM REVOLUT TRADING LTD TO REVOLUT SECURITIES EUROPE UAB,0.0004562,,$0,USD,1.1018\n"
    "2022-08-25T08:27:46.419568Z,TSLA,STOCK SPLIT,0.16431924,,$0,USD,0.0947\n"
    "2025-06-05T07:26:04.809Z,TSLA,BUY - MARKET,0.56217674,€88.94,€50,EUR,1.0000\n"
)


def test_can_handle_revolut_invest() -> None:
    parser = RevolutInvestParser()
    assert parser.can_handle(SAMPLE_CSV) is True


def test_can_handle_rejects_unknown() -> None:
    parser = RevolutInvestParser()
    assert parser.can_handle("Symbol,Type,Quantity\nBTC,Buy,1") is False


def test_skips_cash_movements() -> None:
    parser = RevolutInvestParser()
    activities = parser.parse(SAMPLE_CSV)
    types = {a.type for a in activities}
    # No activity should come from CASH TOP-UP or CASH WITHDRAWAL
    assert all(a.symbol != "" for a in activities)
    tickers = [a.symbol for a in activities]
    assert "CASH" not in tickers


def test_buy_activity() -> None:
    parser = RevolutInvestParser()
    activities = parser.parse(SAMPLE_CSV)
    buys = [a for a in activities if a.type == "BUY" and a.symbol == "O"]
    assert len(buys) == 1
    assert buys[0].quantity == 1.63453043
    assert buys[0].unit_price == 52.07
    assert buys[0].currency == "USD"
    assert buys[0].data_source == "YAHOO"


def test_sell_activity() -> None:
    parser = RevolutInvestParser()
    activities = parser.parse(SAMPLE_CSV)
    sells = [a for a in activities if a.type == "SELL"]
    assert len(sells) == 1
    assert sells[0].symbol == "MA"
    assert sells[0].quantity == 0.1998348
    assert sells[0].unit_price == 402.13


def test_dividend_activity() -> None:
    parser = RevolutInvestParser()
    activities = parser.parse(SAMPLE_CSV)
    divs = [a for a in activities if a.type == "DIVIDEND"]
    assert len(divs) == 1
    assert divs[0].symbol == "MSFT"
    assert divs[0].quantity == 1.0
    assert divs[0].unit_price == 0.08


def test_custody_fee_activity() -> None:
    parser = RevolutInvestParser()
    activities = parser.parse(SAMPLE_CSV)
    fees = [a for a in activities if a.type == "FEE"]
    assert len(fees) == 1
    assert fees[0].unit_price == 0.01
    assert fees[0].data_source == "MANUAL"


def test_stock_split_is_buy() -> None:
    parser = RevolutInvestParser()
    activities = parser.parse(SAMPLE_CSV)
    splits = [a for a in activities if a.symbol == "TSLA" and a.type == "BUY"]
    assert any(a.quantity == 0.16431924 for a in splits)


def test_european_decimal_quantity() -> None:
    # "0,76672417" should parse as 0.76672417, not 76672417
    parser = RevolutInvestParser()
    activities = parser.parse(SAMPLE_CSV)
    msft_buys = [a for a in activities if a.symbol == "MSFT" and a.type == "BUY"]
    assert len(msft_buys) == 1
    assert abs(msft_buys[0].quantity - 0.76672417) < 1e-6


def test_date_utc_normalized() -> None:
    parser = RevolutInvestParser()
    activities = parser.parse(SAMPLE_CSV)
    buy_o = next(a for a in activities if a.symbol == "O")
    assert buy_o.date.year == 2023
    assert buy_o.date.month == 9
    assert buy_o.date.day == 22
    assert buy_o.date.tzinfo is None
