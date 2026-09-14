from django.test import TestCase

from trading.models import Instrument
from trading.services.demo import seed_demo_data
from trading.services.reports import build_asset_report


class AssetReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        seed_demo_data(days=90)

    def test_mcd_report_contains_business_and_chart_histories(self):
        instrument = Instrument.objects.get(symbol="MCD")
        report = build_asset_report(instrument)
        self.assertEqual(report["identity"]["name"], "McDonald's Corporation")
        self.assertEqual(report["headline"]["market_cap_display"], "$211.6B")
        self.assertEqual(len(report["charts"]["revenue"]), 5)
        self.assertEqual(len(report["charts"]["eps"]), 8)
        self.assertTrue(report["quality"]["is_demo"])

    def test_report_without_snapshots_fails_soft(self):
        instrument = Instrument.objects.create(symbol="EMPTY", name="Empty Corp", exchange="TEST")
        report = build_asset_report(instrument)
        self.assertIsNone(report["headline"]["price"])
        self.assertEqual(report["charts"]["revenue"], [])
        self.assertIn("pendiente", report["business"]["moat"].lower())
