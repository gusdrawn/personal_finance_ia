from datetime import date
from decimal import Decimal
from unittest.mock import Mock

from django.test import TestCase

from trading.models import DailyBar, FinancialStatementSnapshot, Instrument
from trading.services.sec import sync_sec_instrument


def fact(tag, unit, values):
    return {tag: {"units": {unit: [{"end": end, "val": value, "form": "10-K", "fp": "FY", "filed": filed} for end, value, filed in values]}}}


class SECSyncTests(TestCase):
    def setUp(self):
        self.instrument = Instrument.objects.create(symbol="MCD", name="MCD", exchange="NYSE", source="YAHOO_FINANCE")
        DailyBar.objects.create(instrument=self.instrument, session_date=date(2026, 8, 28), open=264, high=266, low=263, close=Decimal("265"), source="YAHOO_FINANCE")

    def test_official_facts_create_statements_and_metrics(self):
        gaap = {}
        gaap.update(fact("Revenues", "USD", [("2024-12-31", 25000000000, "2025-02-20"), ("2025-12-31", 26885000000, "2026-02-24")]))
        gaap.update(fact("OperatingIncomeLoss", "USD", [("2025-12-31", 12000000000, "2026-02-24")]))
        gaap.update(fact("NetIncomeLoss", "USD", [("2025-12-31", 8200000000, "2026-02-24")]))
        gaap.update(fact("EarningsPerShareDiluted", "USD/shares", [("2024-12-31", 11.72, "2025-02-20"), ("2025-12-31", 11.95, "2026-02-24")]))
        gaap.update(fact("Assets", "USD", [("2025-12-31", 55000000000, "2026-02-24")]))
        gaap.update(fact("StockholdersEquity", "USD", [("2025-12-31", -3000000000, "2026-02-24")]))
        gaap.update(fact("NetCashProvidedByUsedInOperatingActivities", "USD", [("2025-12-31", 11000000000, "2026-02-24")]))
        gaap.update(fact("PaymentsToAcquirePropertyPlantAndEquipment", "USD", [("2025-12-31", 3000000000, "2026-02-24")]))
        provider = Mock(name="SEC_EDGAR")
        provider.name = "SEC_EDGAR"
        provider.fetch_company_facts.return_value = {"resolved_company": {"cik": "0000063908", "name": "McDonald's Corporation"}, "facts": {"us-gaap": gaap}}
        result = sync_sec_instrument(self.instrument, provider=provider)
        self.assertGreaterEqual(result["statements"], 3)
        income = FinancialStatementSnapshot.objects.get(instrument=self.instrument, statement_type="INCOME", period_end=date(2025, 12, 31))
        self.assertEqual(income.data["revenue"], 26885000000)
        metric = self.instrument.metric_snapshots.get(source="SEC_EDGAR")
        self.assertAlmostEqual(float(metric.pe), 265 / 11.95, places=3)
        self.assertEqual(metric.metrics["price_as_of"], "2026-08-28")

    def test_missing_facts_do_not_create_fake_statement(self):
        provider = Mock(name="SEC_EDGAR")
        provider.name = "SEC_EDGAR"
        provider.fetch_company_facts.return_value = {"resolved_company": {"cik": "0000063908", "name": "McDonald's Corporation"}, "facts": {}}
        result = sync_sec_instrument(self.instrument, provider=provider)
        self.assertEqual(result["statements"], 0)
        self.assertFalse(FinancialStatementSnapshot.objects.exists())
        self.assertTrue(self.instrument.metric_snapshots.filter(source="SEC_EDGAR").exists())
