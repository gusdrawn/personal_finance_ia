from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from trading.models import AnalystSnapshot, DailyBar, FinancialStatementSnapshot, Instrument, InstrumentMetricSnapshot, MarketEvent
from trading.services.live import sync_fmp_events, sync_fmp_instrument
from trading.services.providers.twelve_data import ProviderError


class FakeFMPProvider:
    name = "FMP"
    base_url = "https://financialmodelingprep.com/stable"

    def fetch_profile(self, symbol):
        return {"symbol": symbol, "companyName": "McDonald's Corporation", "exchangeShortName": "NYSE", "currency": "USD", "country": "US", "city": "Chicago", "state": "IL", "sector": "Consumer Cyclical", "industry": "Restaurants", "description": "Global restaurant franchisor", "marketCap": 210_000_000_000, "beta": 0.72, "fullTimeEmployees": 150000}

    def fetch_metrics(self, symbol):
        return {"priceToEarningsRatioTTM": 20.5, "priceToSalesRatioTTM": 7.1, "priceToBookRatioTTM": 8.2, "enterpriseValueMultipleTTM": 17.5, "dividendYieldTTM": 0.024, "grossProfitMarginTTM": 0.57, "operatingProfitMarginTTM": 0.45, "returnOnEquityTTM": 0.31, "debtToEquityRatioTTM": 1.8}

    def fetch_financial_statements(self, symbol):
        income = [
            {"date": "2025-12-31", "period": "FY", "revenue": 26_400_000_000, "ebitda": 12_000_000_000, "netIncome": 8_000_000_000, "epsDiluted": 12.2},
            {"date": "2024-12-31", "period": "FY", "revenue": 25_900_000_000, "ebitda": 11_500_000_000, "netIncome": 7_500_000_000, "epsDiluted": 11.72},
        ]
        balance = [{"date": "2025-12-31", "period": "FY", "totalAssets": 55_000_000_000, "cashAndCashEquivalents": 4_000_000_000, "totalDebt": 38_000_000_000, "totalStockholdersEquity": -3_000_000_000}]
        cashflow = [{"date": "2025-12-31", "period": "FY", "operatingCashFlow": 10_000_000_000, "freeCashFlow": 7_000_000_000, "capitalExpenditure": -3_000_000_000}]
        return {"income": income, "balance": balance, "cashflow": cashflow}

    def fetch_analyst_data(self, symbol):
        return {
            "estimates": [{"date": "2026-12-31", "estimatedEpsAvg": 12.93}],
            "grades": [{"strongBuy": 4, "buy": 12, "hold": 8, "sell": 1, "strongSell": 0}],
            "targets": [{"targetConsensus": 325, "targetHigh": 360, "targetLow": 280}],
        }

    def fetch_events(self, start, end):
        return [{"date": start.isoformat(), "symbol": "MCD", "epsEstimated": 3.15, "epsActual": None}]


class FailingFMPProvider(FakeFMPProvider):
    def fetch_profile(self, symbol):
        raise ProviderError("plan sin acceso")

    fetch_metrics = fetch_profile
    fetch_financial_statements = fetch_profile
    fetch_analyst_data = fetch_profile


class EmptyFMPProvider(FakeFMPProvider):
    def fetch_profile(self, symbol):
        return {}

    def fetch_metrics(self, symbol):
        return {}

    def fetch_financial_statements(self, symbol):
        return {"income": [], "balance": [], "cashflow": []}

    def fetch_analyst_data(self, symbol):
        return {"estimates": [], "grades": [], "targets": []}


class LiveSyncTests(TestCase):
    def setUp(self):
        self.instrument = Instrument.objects.create(symbol="MCD", name="MCD Demo", exchange="NYSE", source="DEMO")
        start = date(2026, 1, 1)
        for index in range(260):
            session = start + timedelta(days=index)
            price = Decimal("250") + Decimal(index) / Decimal("10")
            DailyBar.objects.create(instrument=self.instrument, session_date=session, open=price, high=price + 1, low=price - 1, close=price, adjusted_close=price, volume=1_000_000, source="TWELVE_DATA")

    def test_real_fundamentals_are_normalized_and_persisted(self):
        DailyBar.objects.create(
            instrument=self.instrument,
            session_date=date(2025, 12, 31),
            open=1,
            high=1,
            low=1,
            close=1,
            adjusted_close=1,
            volume=1,
            source="DEMO",
        )
        result = sync_fmp_instrument(self.instrument, provider=FakeFMPProvider())
        self.instrument.refresh_from_db()
        metric = InstrumentMetricSnapshot.objects.get(instrument=self.instrument)
        self.assertEqual(result["statements"], 4)
        self.assertEqual(self.instrument.source, "FMP")
        self.assertEqual(self.instrument.name, "McDonald's Corporation")
        self.assertEqual(metric.source, "FMP")
        self.assertEqual(metric.market_cap, Decimal("210000000000"))
        self.assertEqual(metric.dividend_yield, Decimal("2.4"))
        self.assertEqual(metric.target_mean, Decimal("325"))
        self.assertIsNotNone(metric.rsi14)
        self.assertLess(metric.metrics["technical"]["one_year_return"], 20)
        self.assertEqual(FinancialStatementSnapshot.objects.filter(instrument=self.instrument, source="FMP").count(), 4)
        self.assertEqual(AnalystSnapshot.objects.get(instrument=self.instrument).consensus, "BUY")

    def test_provider_without_entitlement_leaves_unknowns_instead_of_demo(self):
        result = sync_fmp_instrument(self.instrument, provider=FailingFMPProvider())
        self.assertEqual(result["updated"], 0)
        self.assertEqual(len(result["warnings"]), 4)
        self.assertFalse(InstrumentMetricSnapshot.objects.exists())

    def test_empty_provider_response_does_not_create_false_real_snapshot(self):
        result = sync_fmp_instrument(self.instrument, provider=EmptyFMPProvider())
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["warnings"], ["FMP no devolvió datos."])
        self.assertFalse(InstrumentMetricSnapshot.objects.exists())

    def test_real_earnings_calendar_is_persisted_with_source(self):
        total = sync_fmp_events(FakeFMPProvider(), start=timezone.localdate(), end=timezone.localdate() + timedelta(days=7))
        event = MarketEvent.objects.get()
        self.assertEqual(total, 1)
        self.assertEqual(event.event_type, "EARNINGS")
        self.assertIn("FMP", event.notes)
