"""Tests for the DEGIRO parser."""

import pytest
from datetime import datetime

from bot.parsers.degiro import DegiroParser, _parse_amount

HEADER = "Datum,Tijd,Valutadatum,Product,ISIN,Omschrijving,FX,Mutatie,,Saldo,,Order Id\n"


def make_csv(*rows: str) -> str:
    return HEADER + "".join(rows)


def row(
    date="01-01-2024",
    time="10:00",
    product="",
    isin="",
    description="",
    fx="",
    currency="EUR",
    amount="0",
    order_id="",
) -> str:
    return f'{date},{time},{date},{product},{isin},{description},{fx},{currency},{amount},{currency},{amount},{order_id}\n'


@pytest.fixture
def parser():
    return DegiroParser()


# ---------------------------------------------------------------------------
# _parse_amount
# ---------------------------------------------------------------------------

class TestParseAmount:
    def test_european_comma_decimal(self):
        assert _parse_amount("33,90") == pytest.approx(33.90)

    def test_negative_european(self):
        assert _parse_amount("-33,90") == pytest.approx(-33.90)

    def test_dot_decimal(self):
        assert _parse_amount("-97.93") == pytest.approx(-97.93)

    def test_thousands_dot_comma_decimal(self):
        assert _parse_amount("1.234,56") == pytest.approx(1234.56)

    def test_empty_returns_zero(self):
        assert _parse_amount("") == 0.0

    def test_quoted_value(self):
        assert _parse_amount('"-1,00"') == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

class TestDetection:
    def test_detects_degiro_header(self, parser):
        csv_content = make_csv(
            row(isin="IE00B3RBWM25", description='"Koop 1 @ 79,35 EUR"', amount="-79.35", order_id="abc123"),
        )
        assert parser.can_handle(csv_content)

    def test_detects_spanish_header(self, parser):
        csv_content = 'Fecha,Hora,Fecha valor,Producto,ISIN,Descripción,Tipo,Variación,,Saldo,,ID Orden\n'
        assert parser.can_handle(csv_content)

    def test_does_not_detect_wrong_header(self, parser):
        csv_content = 'Date,Ticker,Type,Quantity,Price per share,Total Amount,Currency,FX Rate\n'
        assert not parser.can_handle(csv_content)


# ---------------------------------------------------------------------------
# Buy/sell record pairs
# ---------------------------------------------------------------------------

class TestBuySell:
    def test_buy_with_fee_fee_before_buy(self, parser):
        """Fee record appears before buy record (common DEGIRO ordering)."""
        csv_content = make_csv(
            row(date="15-12-2022", time="16:55", product="VICI", isin="US9256521090",
                description="DEGIRO Transactiekosten en/of kosten van derden",
                currency="EUR", amount='"-1,00"', order_id="order-uuid-1"),
            row(date="15-12-2022", time="16:55", product="VICI", isin="US9256521090",
                description='"Koop 1 @ 33,9 USD"',
                currency="USD", amount='"-33,90"', order_id="order-uuid-1"),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 1
        a = activities[0]
        assert a.type == "BUY"
        assert a.quantity == pytest.approx(1.0)
        assert a.unit_price == pytest.approx(33.9)
        assert a.fee == pytest.approx(1.0)
        assert a.currency == "USD"
        assert a.symbol == "US9256521090"
        assert a.data_source == "YAHOO"
        assert a.comment == "order-uuid-1"

    def test_buy_with_fee_buy_before_fee(self, parser):
        """Buy record appears before fee record."""
        csv_content = make_csv(
            row(date="08-03-2024", time="11:25", product="TOYOTA", isin="JP3633400001",
                description='"Koop 30 @ 22,4 EUR"',
                currency="EUR", amount="-672", order_id="541651641"),
            row(date="08-03-2024", time="11:25", product="TOYOTA", isin="JP3633400001",
                description="DEGIRO Transactiekosten en/of kosten van derden",
                currency="EUR", amount="-1.00", order_id="541651641"),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 1
        a = activities[0]
        assert a.type == "BUY"
        assert a.quantity == pytest.approx(30.0)
        assert a.fee == pytest.approx(1.0)

    def test_sell_with_fee(self, parser):
        csv_content = make_csv(
            row(date="10-08-2021", time="16:18", product="BNS", isin="CA0641491075",
                description="DEGIRO Transactiekosten en/of kosten van derden",
                currency="EUR", amount='"-0,50"', order_id="order-bns"),
            row(date="10-08-2021", time="16:18", product="BNS", isin="CA0641491075",
                description='"Verkoop 1 @ 63,97 USD"',
                currency="USD", amount='"63,97"', order_id="order-bns"),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 1
        a = activities[0]
        assert a.type == "SELL"
        assert a.quantity == pytest.approx(1.0)
        assert a.unit_price == pytest.approx(63.97)
        assert a.fee == pytest.approx(0.50)

    def test_buy_no_fee(self, parser):
        csv_content = make_csv(
            row(date="13-07-2020", time="15:59", product="VWCE", isin="IE00B3RBWM25",
                description='"Koop 1 @ 79,35 EUR"',
                currency="EUR", amount='"-79,35"', order_id="53a00f75-xxxx"),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 1
        a = activities[0]
        assert a.type == "BUY"
        assert a.fee == pytest.approx(0.0)
        assert a.unit_price == pytest.approx(79.35)

    def test_buy_comment_uses_order_id_when_present(self, parser):
        csv_content = make_csv(
            row(date="01-01-2024", time="10:00", isin="US0000000000",
                description='"Koop 1 @ 10 EUR"',
                currency="EUR", amount="-10", order_id="my-order-id"),
        )
        activities = parser.parse(csv_content)
        assert activities[0].comment == "my-order-id"

    def test_buy_comment_fallback_when_no_order_id(self, parser):
        csv_content = make_csv(
            row(date="13-07-2020", time="15:59", isin="IE00B3RBWM25",
                description='"Koop 1 @ 79,35 EUR"',
                currency="EUR", amount='"-79,35"', order_id=""),
        )
        activities = parser.parse(csv_content)
        assert activities[0].comment == "Buy IE00B3RBWM25 @ 13-07-2020T15:59"

    def test_french_buy_achat(self, parser):
        csv_content = make_csv(
            row(date="11-03-2024", time="10:39", product="QT GROUP OYJ", isin="FI4000198031",
                description="Frais DEGIRO de courtage et/ou de parties tierces",
                currency="EUR", amount="-4.90", order_id="cce1bd4c"),
            row(date="11-03-2024", time="10:39", product="QT GROUP OYJ", isin="FI4000198031",
                description='"Achat 6 QT GROUP OYJ@79,96 EUR (FI4000198031)"',
                currency="EUR", amount="-479.76", order_id="cce1bd4c"),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 1
        a = activities[0]
        assert a.type == "BUY"
        assert a.quantity == pytest.approx(6.0)
        assert a.unit_price == pytest.approx(79.96, rel=1e-3)
        assert a.fee == pytest.approx(4.90)

    def test_portuguese_buy_compra(self, parser):
        csv_content = make_csv(
            row(date="02-01-2024", time="14:42", product="ISHARES MSCI WOR A", isin="IE00B4L5Y983",
                description="Comissões de transação DEGIRO e/ou taxas de terceiros",
                currency="EUR", amount="-1.00", order_id="7b377a93"),
            row(date="02-01-2024", time="14:42", product="ISHARES MSCI WOR A", isin="IE00B4L5Y983",
                description='"Compra 1 ISHARES MSCI WOR A@82,055 EUR (IE00B4L5Y983)"',
                currency="EUR", amount="-82.06", order_id="7b377a93"),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 1
        assert activities[0].type == "BUY"
        assert activities[0].quantity == pytest.approx(1.0)

    def test_english_sell(self, parser):
        csv_content = make_csv(
            row(date="02-04-2024", time="09:00", product="AVIVA", isin="GB00BPQY8M80",
                description="Sell 4 AVIVA@496 GBX (GB00BPQY8M80)",
                currency="GBP", amount="19.84", order_id="86c1f17b"),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 1
        a = activities[0]
        assert a.type == "SELL"
        assert a.quantity == pytest.approx(4.0)

    def test_stock_dividend_is_buy(self, parser):
        csv_content = make_csv(
            row(date="14-09-2023", time="08:49", product="PROSUS NV", isin="NL0013654783",
                description="STOCK DIVIDEND: Koop 999 @ 0 EUR",
                currency="EUR", amount="0.00", order_id=""),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 1
        a = activities[0]
        assert a.type == "BUY"
        assert a.quantity == pytest.approx(999.0)
        assert a.unit_price == pytest.approx(0.0)

    def test_multiple_buys_different_order_ids(self, parser):
        """Two buy records with different order IDs each get paired with their own fee."""
        csv_content = make_csv(
            row(date="01-08-2023", time="10:03", isin="IE00BYXG2H39",
                description="DEGIRO Transactiekosten en/of kosten van derden",
                currency="EUR", amount="-1.00", order_id="aaa"),
            row(date="01-08-2023", time="10:03", isin="IE00BYXG2H39",
                description='"Verkoop 1 @ 5,416 EUR"',
                currency="EUR", amount="5.42", order_id="aaa"),
            row(date="25-07-2023", time="14:31", isin="IE00B0M63391",
                description="DEGIRO Transactiekosten en/of kosten van derden",
                currency="EUR", amount="-1.00", order_id="bbb"),
            row(date="25-07-2023", time="14:31", isin="IE00B0M63391",
                description='"Koop 1 @ 42,53 EUR"',
                currency="EUR", amount="-42.53", order_id="bbb"),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 2
        assert {a.type for a in activities} == {"BUY", "SELL"}
        for a in activities:
            assert a.fee == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Dividend records
# ---------------------------------------------------------------------------

class TestDividend:
    def test_dividend_with_tax_normal_order(self, parser):
        """Dividend row appears before withholding tax row."""
        csv_content = make_csv(
            row(date="16-12-2022", time="09:05", product="COCA-COLA", isin="US1912161007",
                description="Dividend", currency="USD", amount='"0,44"'),
            row(date="16-12-2022", time="09:05", product="COCA-COLA", isin="US1912161007",
                description="Dividendbelasting", currency="USD", amount='"-0,07"'),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 1
        a = activities[0]
        assert a.type == "DIVIDEND"
        assert a.unit_price == pytest.approx(0.44)
        assert a.fee == pytest.approx(0.07)
        assert a.quantity == 1
        assert a.currency == "USD"
        assert a.symbol == "US1912161007"
        assert a.comment.startswith("Dividend US1912161007 @ 16-12-2022T")

    def test_dividend_with_tax_reversed_order(self, parser):
        """Tax row appears before dividend row (real DEGIRO export variant)."""
        csv_content = make_csv(
            row(date="01-08-2024", time="07:40", product="OXFORD SQUARE", isin="US69181V1070",
                description="Impôts sur dividende", currency="USD", amount="-0.53"),
            row(date="01-08-2024", time="07:39", product="OXFORD SQUARE", isin="US69181V1070",
                description="Dividende", currency="USD", amount="3.50"),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 1
        a = activities[0]
        assert a.type == "DIVIDEND"
        assert a.unit_price == pytest.approx(3.50)
        assert a.fee == pytest.approx(0.53)

    def test_dividend_without_tax(self, parser):
        csv_content = make_csv(
            row(date="02-01-2024", time="06:58", product="ISHARES S&P 500", isin="IE0031442068",
                description="Dividende", currency="USD", amount="11.82"),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 1
        a = activities[0]
        assert a.type == "DIVIDEND"
        assert a.fee == pytest.approx(0.0)
        assert a.unit_price == pytest.approx(11.82)

    def test_japanese_yen_dividend(self, parser):
        csv_content = make_csv(
            row(date="27-05-2024", time="07:41", product="TOYOTA MOTOR", isin="JP3633400001",
                description="Dividend", currency="JPY", amount="9999.99"),
            row(date="27-05-2024", time="07:41", product="TOYOTA MOTOR", isin="JP3633400001",
                description="Dividendbelasting", currency="JPY", amount="-9999.99"),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 1
        assert activities[0].currency == "JPY"


# ---------------------------------------------------------------------------
# Platform fees and interest
# ---------------------------------------------------------------------------

class TestPlatformFeeAndInterest:
    def test_aansluitingskosten(self, parser):
        csv_content = make_csv(
            row(date="05-02-2024", time="07:16",
                description="DEGIRO Aansluitingskosten 2024 (Nasdaq - NDQ)",
                currency="EUR", amount="-2.50"),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 1
        a = activities[0]
        assert a.type == "FEE"
        assert a.unit_price == pytest.approx(2.50)
        assert a.fee == pytest.approx(0.0)
        assert a.data_source == "MANUAL"

    def test_connection_fee(self, parser):
        csv_content = make_csv(
            row(date="05-02-2024", time="07:18",
                description="Giro Exchange Connection Fee 2024",
                currency="EUR", amount="-2.50"),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 1
        assert activities[0].type == "FEE"

    def test_corporate_action(self, parser):
        csv_content = make_csv(
            row(date="04-10-2024", time="07:27",
                description="DEGIRO Corporate Action Kosten",
                currency="USD", amount="-0.01"),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 1
        assert activities[0].type == "FEE"

    def test_interest(self, parser):
        csv_content = make_csv(
            row(date="23-08-2024", time="12:21",
                description="DEGIRO courtesy",
                currency="EUR", amount="0.24"),
            row(date="23-08-2024", time="12:21",
                description="DEGIRO courtesy",
                currency="EUR", amount="0.61"),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 2
        for a in activities:
            assert a.type == "INTEREST"
            assert a.data_source == "MANUAL"


# ---------------------------------------------------------------------------
# Ignored records
# ---------------------------------------------------------------------------

class TestIgnored:
    @pytest.mark.parametrize("description", [
        "iDEAL Deposit",
        "Reservation iDEAL / Sofort Deposit",
        "Degiro Cash Sweep Transfer",
        "Processed Flatex Withdrawal",
        "flatex terugstorting",
        "Valuta Creditering",
        "Valuta Debitering",
        "Overboeking van uw geldrekening bij flatexDEGIRO Bank 26,45 EUR",
        "PRODUCTWIJZIGING : Koop 500 @ 0,039 EUR",
        "FX Credit",
        "FX Debit",
        "Variation Fonds Monétaires (EUR)",
        "Ingreso Cambio de Divisa",
        "Retirada Cambio de Divisa",
        "Levantamento de divisa",
    ])
    def test_ignored_records_produce_no_activities(self, parser, description):
        csv_content = make_csv(
            row(description=description, currency="EUR", amount="1.00"),
        )
        activities = parser.parse(csv_content)
        assert activities == [], f"Expected no activities for: {description}"

    def test_empty_description_skipped(self, parser):
        # Continuation rows from multi-line order IDs have empty descriptions
        csv_content = make_csv(
            row(description="", currency="EUR", amount="0"),
        )
        assert parser.parse(csv_content) == []


# ---------------------------------------------------------------------------
# GBX → GBp currency conversion
# ---------------------------------------------------------------------------

class TestGbxConversion:
    def test_gbx_converted_to_gbp(self, parser):
        # The amount in GBP column but description says GBX — currency column maps to actual trade currency
        csv_content = make_csv(
            row(date="03-03-2020", time="11:14", product="ISHARES", isin="IE00B1XNHC34",
                description='"Koop 475 @ 583,5 GBX"',
                currency="GBP", amount="-2771.63", order_id="3b000105"),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 1
        # Currency in the CSV is GBP (not GBX) for this case
        assert activities[0].currency == "GBP"

    def test_gbx_in_currency_column_converted(self, parser):
        # If the Mutatie column actually says GBX, it must be converted
        csv_content = make_csv(
            row(date="01-01-2024", time="10:00", isin="GB0000000001",
                description='"Koop 10 @ 500 GBX"',
                currency="GBX", amount="-5000"),
        )
        activities = parser.parse(csv_content)
        assert len(activities) == 1
        assert activities[0].currency == "GBp"


# ---------------------------------------------------------------------------
# Full sample file smoke test
# ---------------------------------------------------------------------------

class TestSampleFile:
    def test_sample_file_parses_without_error(self, parser):
        import pathlib
        sample = pathlib.Path(
            "examples/Export-To-Ghostfolio/samples/degiro-export.csv"
        )
        if not sample.exists():
            pytest.skip("Sample file not found")
        activities = parser.parse(sample.read_text(encoding="utf-8"))
        # Should produce a reasonable number of activities (buy/sell/dividend/fee/interest)
        assert len(activities) >= 20
        types = {a.type for a in activities}
        assert "BUY" in types
        assert "DIVIDEND" in types
        assert "FEE" in types
        assert "INTEREST" in types
