"""Tests for the Delta portfolio tracker CSV parser."""

from bot.parsers.delta import DeltaParser

SAMPLE_CSV = (
    "Date,Way,Base amount,Base currency (name),Base type,"
    "Quote amount,Quote currency,Exchange,Sent/Received from,Sent to,"
    "Fee amount,Fee currency (name),Broker,Notes\n"
    "2023-06-02 16:06:47-04:00,BUY,10,ETH,CRYPTO,0.6,BTC,Binance,,,0.1,BNB,,Crypto buy\n"
    "2023-06-01 19:31:54-04:00,SELL,100,SOL (Solana),CRYPTO,0.88,ETH,Binance,,,,,,Crypto sell\n"
    "2023-05-26 02:08:15-04:00,WITHDRAW,0.1,DOT (Polkadot),CRYPTO,,,,MY_WALLET,OTHER,0.001,DOT,,Withdrawal\n"
    "2023-05-09 14:51:31-04:00,DEPOSIT,0.001,BTC,CRYPTO,,,,DIVIDENDS,MY_WALLET,,,,Deposit\n"
    "2023-05-08 15:00:00-04:00,BUY,5,AAPL,STOCK,1250,USD,Nasdaq,,,,,eToro,Stock purchase\n"
    "2023-05-08 15:00:00-04:00,DEPOSIT,5000,USD,FIAT,,,,,,,,eToro,Fiat deposit\n"
    "2023-05-08 15:00:00-04:00,DIVIDEND,,AAPL,STOCK,2.5,USD,Nasdaq,,,0.5,USD,eToro,Dividend\n"
)

SELL_CSV = (
    "Date,Way,Base amount,Base currency (name),Base type,"
    "Quote amount,Quote currency,Exchange,Sent/Received from,Sent to,"
    "Fee amount,Fee currency (name),Broker,Notes\n"
    "2023-07-10 10:00:00+00:00,SELL,3,MSFT,STOCK,900,USD,Nasdaq,,,1.5,USD,,Sell MSFT\n"
)

GBX_CSV = (
    "Date,Way,Base amount,Base currency (name),Base type,"
    "Quote amount,Quote currency,Exchange,Sent/Received from,Sent to,"
    "Fee amount,Fee currency (name),Broker,Notes\n"
    "2023-08-01 09:00:00+00:00,BUY,100,LLOY,STOCK,6000,GBX,LSE,,,,,,Buy Lloyds\n"
)


def test_can_handle_delta() -> None:
    parser = DeltaParser()
    assert parser.can_handle(SAMPLE_CSV) is True


def test_can_handle_rejects_unknown() -> None:
    parser = DeltaParser()
    assert parser.can_handle("Date,Ticker,Amount\n2024-01-01,AAPL,100") is False


def test_skips_cross_crypto() -> None:
    # ETH/BTC and SOL/ETH have no fiat quote → skip
    parser = DeltaParser()
    activities = parser.parse(SAMPLE_CSV)
    symbols = [a.symbol for a in activities]
    assert "ETH" not in symbols
    assert "SOL" not in symbols
    assert "DOT" not in symbols


def test_skips_deposit_withdraw() -> None:
    parser = DeltaParser()
    activities = parser.parse(SAMPLE_CSV)
    # Only AAPL BUY and AAPL DIVIDEND survive (crypto rows are cross-crypto → skipped)
    assert len(activities) == 2


def test_crypto_fiat_quote_imported() -> None:
    csv = (
        "Date,Way,Base amount,Base currency (name),Base type,"
        "Quote amount,Quote currency,Exchange,Sent/Received from,Sent to,"
        "Fee amount,Fee currency (name),Broker,Notes\n"
        "2024-01-15T10:00:00.000Z,BUY,0.01,BTC (Bitcoin),CRYPTO,600,EUR,Kraken,,,,,, \n"
    )
    parser = DeltaParser()
    activities = parser.parse(csv)
    assert len(activities) == 1
    act = activities[0]
    assert act.symbol == "BTC-EUR"
    assert act.currency == "EUR"
    assert act.data_source == "YAHOO"
    assert act.quantity == 0.01
    assert act.unit_price == 60000.0  # 600 / 0.01


def test_buy_stock() -> None:
    parser = DeltaParser()
    activities = parser.parse(SAMPLE_CSV)
    buy = next(a for a in activities if a.type == "BUY")

    assert buy.symbol == "AAPL"
    assert buy.quantity == 5.0
    assert buy.unit_price == 250.0  # 1250 / 5
    assert buy.currency == "USD"
    assert buy.data_source == "YAHOO"
    assert buy.fee == 0.0


def test_dividend_stock() -> None:
    parser = DeltaParser()
    activities = parser.parse(SAMPLE_CSV)
    div = next(a for a in activities if a.type == "DIVIDEND")

    assert div.symbol == "AAPL"
    assert div.quantity == 1.0
    assert div.unit_price == 2.5
    assert div.fee == 0.5
    assert div.currency == "USD"


def test_sell_stock() -> None:
    parser = DeltaParser()
    activities = parser.parse(SELL_CSV)

    assert len(activities) == 1
    sell = activities[0]
    assert sell.type == "SELL"
    assert sell.symbol == "MSFT"
    assert sell.quantity == 3.0
    assert sell.unit_price == 300.0  # 900 / 3
    assert sell.fee == 1.5


def test_symbol_parenthetical_stripped() -> None:
    csv = (
        "Date,Way,Base amount,Base currency (name),Base type,"
        "Quote amount,Quote currency,Exchange,Sent/Received from,Sent to,"
        "Fee amount,Fee currency (name),Broker,Notes\n"
        "2023-05-08 15:00:00-04:00,BUY,10,BNS (Bank of Nova Scotia),STOCK,600,CAD,TSX,,,,,, \n"
    )
    parser = DeltaParser()
    activities = parser.parse(csv)
    assert activities[0].symbol == "BNS"


def test_gbx_converted_to_gbp() -> None:
    parser = DeltaParser()
    activities = parser.parse(GBX_CSV)
    assert activities[0].currency == "GBp"


def test_date_parsed_and_utc_normalized() -> None:
    parser = DeltaParser()
    activities = parser.parse(SAMPLE_CSV)
    buy = next(a for a in activities if a.type == "BUY")
    # 2023-05-08 15:00:00-04:00 → 2023-05-08 19:00:00 UTC
    assert buy.date.year == 2023
    assert buy.date.month == 5
    assert buy.date.day == 8
    assert buy.date.hour == 19
    assert buy.date.tzinfo is None  # stored as naive UTC


def test_comment_preserved() -> None:
    parser = DeltaParser()
    activities = parser.parse(SAMPLE_CSV)
    buy = next(a for a in activities if a.type == "BUY")
    assert buy.comment == "Stock purchase"
