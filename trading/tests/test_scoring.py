from decimal import Decimal

from django.test import SimpleTestCase

from trading.services.scoring import calculate_quant_score, fair_value_upside


class QuantScoreTests(SimpleTestCase):
    def test_score_requires_coverage_and_returns_components(self):
        result = calculate_quant_score({"pe": 10, "roe": 20, "revenue_growth": 15, "rsi14": 55, "volatility_30d": 15}, asset_type="STOCK")
        self.assertIsNotNone(result["score"])
        self.assertGreaterEqual(result["coverage"], 60)
        self.assertIn("value", result["components"])

    def test_missing_data_is_unknown_not_zero(self):
        result = calculate_quant_score({"pe": 12}, asset_type="STOCK")
        self.assertIsNone(result["score"])
        self.assertLess(result["coverage"], 60)
        self.assertEqual(fair_value_upside(Decimal("100"), None), None)
