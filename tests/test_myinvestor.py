"""Tests for MyInvestor XLS parser."""

from datetime import UTC

import pytest

from bot.parsers.myinvestor import MyInvestorParser


def _make_xls(*rows: tuple) -> bytes:
    """Build a minimal MyInvestor fondos-style HTML/XLS byte fixture (ISO-8859-1)."""
    header = (
        "<tr>"
        "<th>F.Operaci\xf3n</th><th>F.Liquidaci\xf3n</th><th>Operaci\xf3n</th>"
        "<th>Mercado</th><th>Tipo</th><th>ISIN</th><th>Valor</th>"
        "<th>T\xedtulos/NOMINAL</th><th>Divisa</th><th>Precio Neto</th><th>Importe neto</th>"
        "</tr>"
    )
    data = "".join(
        "<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>"
        for row in rows
    )
    html = f"<html><body><table>{header}{data}</table></body></html>"
    return html.encode("iso-8859-1")


# One row of each operation type for shared fixture
_XLS = _make_xls(
    ("2024-11-29", "2024-11-29", "185322506", "FONDOS EXTRANJEROS", "SUSCRIPCION",      "FR0000989626", "Fund A", "10.5",  "EUR", "150.25", "1577.63"),
    ("2024-10-15", "2024-10-15", "184816142", "FONDOS EXTRANJEROS", "REEMBOLSO",         "IE00B246KL88", "Fund B", "19.45", "EUR", "156.82", "3050.14"),
    ("2024-09-01", "2024-09-01", "183000001", "FONDOS EXTRANJEROS", "SUSCR.POR TRASPASO I", "IE0032620787", "Fund C", "3.1",  "EUR",  "32.23",   "99.91"),
    ("2024-08-20", "2024-08-20", "182000002", "FONDOS EXTRANJEROS", "REEMB.POR TRASPASO I", "LU0159052710", "Fund D", "0.539","EUR", "625.70",  "337.25"),
)


# ── Detection ──────────────────────────────────────────────────────────────────

def test_detect_binary_fondos() -> None:
    assert MyInvestorParser.detect_binary(_XLS) == 1.0


def test_detect_binary_rejects_salida() -> None:
    salida = b"<html><body><table><tr><th>Intervencion</th><th>Contravalor EUR</th></tr></table></body></html>"
    assert MyInvestorParser.detect_binary(salida) == 0.0


def test_detect_binary_rejects_csv() -> None:
    csv = b"Date,Ticker,Type,Quantity\n2024-01-01,AAPL,BUY,10\n"
    assert MyInvestorParser.detect_binary(csv) == 0.0


def test_detect_csv_returns_zero() -> None:
    assert MyInvestorParser.detect("Date,Ticker,Type,Quantity,Price,Total,Currency,FX") == 0.0


# ── Operation type mapping ─────────────────────────────────────────────────────

def test_suscripcion_is_buy() -> None:
    parser = MyInvestorParser()
    activities = parser.parse_binary(_XLS)
    buys = [a for a in activities if a.symbol == "FR0000989626"]
    assert len(buys) == 1
    assert buys[0].type == "BUY"


def test_reembolso_is_sell() -> None:
    parser = MyInvestorParser()
    activities = parser.parse_binary(_XLS)
    sells = [a for a in activities if a.symbol == "IE00B246KL88"]
    assert len(sells) == 1
    assert sells[0].type == "SELL"


def test_traspaso_suscripcion_is_buy() -> None:
    parser = MyInvestorParser()
    activities = parser.parse_binary(_XLS)
    act = next(a for a in activities if a.symbol == "IE0032620787")
    assert act.type == "BUY"


def test_traspaso_reembolso_is_sell() -> None:
    parser = MyInvestorParser()
    activities = parser.parse_binary(_XLS)
    act = next(a for a in activities if a.symbol == "LU0159052710")
    assert act.type == "SELL"


def test_unknown_operation_skipped() -> None:
    xls = _make_xls(
        ("2024-01-01", "2024-01-01", "999", "FONDOS EXTRANJEROS", "TRASPASO_EXTERNO",
         "FR0000989626", "Fund A", "1.0", "EUR", "100.0", "100.0"),
    )
    parser = MyInvestorParser()
    activities = parser.parse_binary(xls)
    assert len(activities) == 0


# ── Fields ─────────────────────────────────────────────────────────────────────

def test_total_activity_count() -> None:
    parser = MyInvestorParser()
    activities = parser.parse_binary(_XLS)
    assert len(activities) == 4


def test_isin_is_symbol() -> None:
    parser = MyInvestorParser()
    activities = parser.parse_binary(_XLS)
    assert all(len(a.symbol) == 12 for a in activities)
    assert {a.symbol for a in activities} == {
        "FR0000989626", "IE00B246KL88", "IE0032620787", "LU0159052710"
    }


def test_data_source_is_yahoo() -> None:
    parser = MyInvestorParser()
    activities = parser.parse_binary(_XLS)
    assert all(a.data_source == "YAHOO" for a in activities)


def test_fee_is_zero() -> None:
    parser = MyInvestorParser()
    activities = parser.parse_binary(_XLS)
    assert all(a.fee == 0.0 for a in activities)


def test_currency_is_eur() -> None:
    parser = MyInvestorParser()
    activities = parser.parse_binary(_XLS)
    assert all(a.currency == "EUR" for a in activities)


def test_quantity_and_price_parsed() -> None:
    parser = MyInvestorParser()
    activities = parser.parse_binary(_XLS)
    act = next(a for a in activities if a.symbol == "FR0000989626")
    assert act.quantity == 10.5
    assert act.unit_price == 150.25


def test_date_parsed() -> None:
    parser = MyInvestorParser()
    activities = parser.parse_binary(_XLS)
    act = next(a for a in activities if a.symbol == "FR0000989626")
    assert act.date.year == 2024
    assert act.date.month == 11
    assert act.date.day == 29


def test_date_is_utc() -> None:
    parser = MyInvestorParser()
    activities = parser.parse_binary(_XLS)
    for a in activities:
        assert a.date.tzinfo == UTC


# ── Error cases ────────────────────────────────────────────────────────────────

def test_parse_csv_raises() -> None:
    parser = MyInvestorParser()
    with pytest.raises(ValueError, match="XLS"):
        parser.parse("Date,Ticker,Type\n2024-01-01,AAPL,BUY")


def test_wrong_file_raises_clear_error() -> None:
    salida = b"<html><body><table><tr><th>A</th><th>B</th></tr><tr><td>x</td><td>y</td></tr></table></body></html>"
    parser = MyInvestorParser()
    with pytest.raises(ValueError, match="fondos"):
        parser.parse_binary(salida)
