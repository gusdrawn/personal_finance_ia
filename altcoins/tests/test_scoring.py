from decimal import Decimal

from django.test import SimpleTestCase

from altcoins.services.scoring import calculate_fundamental_score, calculate_institutional_score, trap_signals


class AltcoinScoringTest(SimpleTestCase):
    def test_score_excluye_sin_datos_y_marca_cobertura(self):
        result = calculate_fundamental_score({"SUPPLY": "PASS", "FEES": "UNKNOWN", "GROWTH": "PARTIAL"})
        self.assertEqual(result["score"], Decimal("75.00"))
        self.assertEqual(result["coverage_pct"], Decimal("25.00"))
        self.assertEqual(result["classification"], "SIN_DATOS")

    def test_trampa_no_modifica_score(self):
        statuses = {"SUPPLY": "FAIL", "ATH_DISCOUNT": "PASS"}
        result = calculate_fundamental_score(statuses)
        self.assertEqual(result["score"], Decimal("50.00"))
        self.assertEqual(trap_signals(statuses), ["DILUTION_RISK"])

    def test_score_institucional_es_independiente(self):
        result = calculate_institutional_score({"ACCOUNTABILITY": "PASS", "INSTITUTIONAL_FOOTPRINT": "PARTIAL"})
        self.assertEqual(result["score"], Decimal("75.00"))
        self.assertEqual(result["coverage_pct"], Decimal("40.00"))
