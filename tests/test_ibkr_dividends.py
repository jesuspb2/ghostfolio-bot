"""Tests for IBKR Dividends parser."""

from bot.parsers.ibkr.dividends import IbkrDividendsParser

SAMPLE_CSV = (
    '"Type","SettleDate","ISIN","Description","Amount","CurrencyPrimary"\n'
    '"Dividends","20230623","US9220427424",'
    '"VT(US9220427424) CASH DIVIDEND USD 0.6504 PER SHARE (Ordinary Dividend)","137.23","USD"\n'
    '"Withholding Tax","20230913","CH0111762537",'
    '"SMMCHA(80486947) CASH DIVIDEND CHF 5.99 PER SHARE - CH TAX","-14.68","CHF"\n'
    '"Withholding Tax","20230623","US9220427424",'
    '"VT(US9220427424) CASH DIVIDEND USD 0.6504 PER SHARE - US TAX","-20.58","USD"\n'
    '"Withholding Tax","20230921","US9220427424",'
    '"VT(US9220427424) CASH DIVIDEND USD 0.4055 PER SHARE - US TAX","-12.9","USD"\n'
    '"Withholding Tax","20231221","US9220427424",'
    '"VT(US9220427424) CASH DIVIDEND USD 0.8008 PER SHARE - US TAX","-25.47","USD"\n'
    '"Dividends","20230913","CH0111762537",'
    '"SMMCHA(CH0111762537) CASH DIVIDEND CHF 0.69 PER SHARE (Return of Capital)","4.83","CHF"\n'
    '"Dividends","20230913","CH0111762537",'
    '"SMMCHA(80486947) CASH DIVIDEND CHF 5.99 PER SHARE (Ordinary Dividend)","41.93","CHF"\n'
    '"Dividends","20230921","US9220427424",'
    '"VT(US9220427424) CASH DIVIDEND USD 0.4055 PER SHARE (Ordinary Dividend)","85.97","USD"\n'
    '"Dividends","20231221","US9220427424",'
    '"VT(US9220427424) CASH DIVIDEND USD 0.8008 PER SHARE (Ordinary Dividend)","169.77","USD"\n'
    '"Payment In Lieu Of Dividends","20250625","JP3546800008",'
    '"4543.T(JP3546800008) PAYMENT IN LIEU OF DIVIDEND (Ordinary Dividend)","455","JPY"\n'
    '"Payment In Lieu Of Dividends","20250630","US11135F1012",'
    '"AVGO(US11135F1012) PAYMENT IN LIEU OF DIVIDEND (Ordinary Dividend)","0.59","USD"\n'
)


def test_can_handle() -> None:
    parser = IbkrDividendsParser()
    assert parser.can_handle(SAMPLE_CSV) is True


def test_can_handle_rejects_trades() -> None:
    parser = IbkrDividendsParser()
    trades_header = (
        '"Buy/Sell","TradeDate","ISIN","Quantity","TradePrice","TradeMoney",'
        '"CurrencyPrimary","IBCommission","IBCommissionCurrency"\n'
    )
    assert parser.can_handle(trades_header) is False


def test_total_activities() -> None:
    # 5 matched/unmatched dividends + 2 PIL = 7
    parser = IbkrDividendsParser()
    activities = parser.parse(SAMPLE_CSV)
    assert len(activities) == 7


def test_all_activities_are_dividend_type() -> None:
    parser = IbkrDividendsParser()
    activities = parser.parse(SAMPLE_CSV)
    assert all(a.type == "DIVIDEND" for a in activities)


def test_withholding_tax_matched_as_fee() -> None:
    parser = IbkrDividendsParser()
    activities = parser.parse(SAMPLE_CSV)
    # VT dividend on 20230623 should carry the US TAX as fee
    vt_jun = next(
        a for a in activities if a.symbol == "VT" and a.date.month == 6
    )
    assert abs(vt_jun.fee - 20.58) < 1e-6


def test_unmatched_dividend_has_zero_fee() -> None:
    parser = IbkrDividendsParser()
    activities = parser.parse(SAMPLE_CSV)
    # SMMCHA (CH0111762537) Return of Capital has no matching tax row
    smmcha_roc = next(
        a for a in activities
        if a.symbol == "SMMCHA" and abs(a.unit_price - 0.69) < 1e-4
    )
    assert smmcha_roc.fee == 0.0


def test_smmcha_ordinary_dividend_has_tax() -> None:
    parser = IbkrDividendsParser()
    activities = parser.parse(SAMPLE_CSV)
    smmcha_ord = next(
        a for a in activities
        if a.symbol == "SMMCHA" and abs(a.unit_price - 5.99) < 0.1
    )
    assert abs(smmcha_ord.fee - 14.68) < 1e-6


def test_ticker_extracted_from_description() -> None:
    parser = IbkrDividendsParser()
    activities = parser.parse(SAMPLE_CSV)
    symbols = {a.symbol for a in activities}
    assert "VT" in symbols
    assert "SMMCHA" in symbols
    assert "AVGO" in symbols


def test_ticker_with_dot_extracted() -> None:
    parser = IbkrDividendsParser()
    activities = parser.parse(SAMPLE_CSV)
    jap = next(a for a in activities if a.symbol == "4543.T")
    assert jap.currency == "JPY"


def test_quantity_computed_from_price_per_share() -> None:
    parser = IbkrDividendsParser()
    activities = parser.parse(SAMPLE_CSV)
    # VT Jun 2023: amount=137.23, price_per_share=0.6504
    # quantity ≈ 137.23 / 0.6504 ≈ 211.0
    vt_jun = next(
        a for a in activities if a.symbol == "VT" and a.date.month == 6
    )
    assert vt_jun.quantity > 1.0
    assert abs(vt_jun.unit_price - 0.6504) < 1e-4


def test_payment_in_lieu_parsed() -> None:
    parser = IbkrDividendsParser()
    activities = parser.parse(SAMPLE_CSV)
    pil = [a for a in activities if a.symbol == "AVGO"]
    assert len(pil) == 1
    assert pil[0].type == "DIVIDEND"


def test_date_parsed() -> None:
    parser = IbkrDividendsParser()
    activities = parser.parse(SAMPLE_CSV)
    vt_jun = next(a for a in activities if a.symbol == "VT" and a.date.month == 6)
    assert vt_jun.date.year == 2023
    assert vt_jun.date.day == 23


def test_gbx_converted_to_gbp() -> None:
    csv = (
        '"Type","SettleDate","ISIN","Description","Amount","CurrencyPrimary"\n'
        '"Dividends","20230101","GB00B3X7QG63",'
        '"VUKE(GB00B3X7QG63) CASH DIVIDEND GBX 5.60 PER SHARE (Ordinary Dividend)","560","GBX"\n'
    )
    parser = IbkrDividendsParser()
    activities = parser.parse(csv)
    assert activities[0].currency == "GBp"
