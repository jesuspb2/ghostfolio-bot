"""Tests for IBKR Trades parser."""

from bot.parsers.ibkr.trades import IbkrTradesParser

SAMPLE_CSV = (
    '"Buy/Sell","TradeDate","ISIN","Quantity","TradePrice","TradeMoney",'
    '"CurrencyPrimary","IBCommission","IBCommissionCurrency"\n'
    '"BUY","20230522","CH0111762537","7","282.7","1978.9","CHF","-5","CHF"\n'
    '"BUY","20230522","US9220427424","95","93.78","8909.1","USD","-1","USD"\n'
    '"BUY","20230609","US9220427424","105","95.42","10019.1","USD","-1","USD"\n'
    '"BUY","20230803","US9220427424","1","97.84","97.84","USD","-0.979","USD"\n'
    '"SELL","20230522","","-8000","1.1173","-8938.4","USD","-1.79924","CHF"\n'
    '"SELL","20230609","","-10012","1.10725","-11085.787","USD","-1.79802","CHF"\n'
)


def test_can_handle() -> None:
    parser = IbkrTradesParser()
    assert parser.can_handle(SAMPLE_CSV) is True


def test_can_handle_rejects_unknown() -> None:
    parser = IbkrTradesParser()
    assert parser.can_handle("Symbol,Type,Quantity\nBTC,Buy,1") is False


def test_skips_rows_without_isin() -> None:
    parser = IbkrTradesParser()
    activities = parser.parse(SAMPLE_CSV)
    # Two SELL rows have empty ISIN → skipped
    sells = [a for a in activities if a.type == "SELL"]
    assert len(sells) == 0


def test_buy_count() -> None:
    parser = IbkrTradesParser()
    activities = parser.parse(SAMPLE_CSV)
    buys = [a for a in activities if a.type == "BUY"]
    assert len(buys) == 4


def test_buy_fields() -> None:
    parser = IbkrTradesParser()
    activities = parser.parse(SAMPLE_CSV)
    first_buy = next(a for a in activities if a.symbol == "CH0111762537")
    assert first_buy.type == "BUY"
    assert first_buy.quantity == 7.0
    assert first_buy.unit_price == 282.7
    assert first_buy.fee == 5.0
    assert first_buy.currency == "CHF"
    assert first_buy.data_source == "YAHOO"


def test_fee_is_absolute_value() -> None:
    parser = IbkrTradesParser()
    activities = parser.parse(SAMPLE_CSV)
    # IBCommission is -0.979 → fee should be 0.979
    small_buy = next(a for a in activities if a.unit_price == 97.84)
    assert abs(small_buy.fee - 0.979) < 1e-6


def test_date_parsed() -> None:
    parser = IbkrTradesParser()
    activities = parser.parse(SAMPLE_CSV)
    buy = next(a for a in activities if a.symbol == "CH0111762537")
    assert buy.date.year == 2023
    assert buy.date.month == 5
    assert buy.date.day == 22


def test_gbx_converted_to_gbp() -> None:
    csv = (
        '"Buy/Sell","TradeDate","ISIN","Quantity","TradePrice","TradeMoney",'
        '"CurrencyPrimary","IBCommission","IBCommissionCurrency"\n'
        '"BUY","20230101","GB00B3X7QG63","10","8550","85500","GBX","-2","GBX"\n'
    )
    parser = IbkrTradesParser()
    activities = parser.parse(csv)
    assert activities[0].currency == "GBp"


def test_header_only_report_returns_no_activities() -> None:
    header_only = SAMPLE_CSV.splitlines()[0] + "\n"

    activities = IbkrTradesParser().parse(header_only)

    assert activities == []
