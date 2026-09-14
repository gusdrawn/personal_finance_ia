from django.contrib.auth.models import User
from django.test import TestCase

from altcoins.models import AltcoinCriterionAssessment, AltcoinDiscoveryCandidate, AltcoinProject
from altcoins.services.discovery import build_research_packet, promote_candidate
from altcoins.services.fundamentals import FundamentalEvaluationError, evaluate_fundamentals


class FakeDetails:
    def fetch_details(self, external_id):
        return {
            "name": "Evaluable",
            "symbol": "EVA",
            "description": {"en": "Documentación"},
            "links": {"homepage": ["https://evaluable.example.com"]},
            "market_data": {"market_cap": {"usd": 100}, "fully_diluted_valuation": {"usd": 120}, "ath_change_percentage": {"usd": -70}, "circulating_supply": 900, "max_supply": 1000},
            "community_data": {}, "developer_data": {}, "categories": [],
        }


class FakeEvaluator:
    def evaluate(self, payload, user):
        evidence_id = payload["allowed_evidence_ids"][0]
        return {"criteria": {code: {"status": "PASS" if code in {"GROWTH", "NARRATIVE"} else "UNKNOWN", "confidence": 80, "rationale": "Evidencia", "evidence_ids": [evidence_id] if code == "GROWTH" else []} for code in ("SUPPLY", "FEES", "GROWTH", "VALUE_CAPTURE", "NARRATIVE", "ATH_DISCOUNT", "EXTERNAL_DEMAND", "VALUATION")}}


class FundamentalsEvaluationTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fundamental", password="secret")
        self.candidate = AltcoinDiscoveryCandidate.objects.create(source="COINGECKO_SEARCH", external_id="evaluable", name="Evaluable", symbol="EVA", coingecko_id="evaluable")
        self.project = promote_candidate(self.candidate)

        def fetcher(url):
            return {"url": url, "content_type": "text/html", "body": b"<p>Evidence</p>", "content_hash": "c" * 64}

        build_research_packet(self.candidate, details_provider=FakeDetails(), fetcher=fetcher)

    def test_evaluacion_guarda_criterios_y_no_borra_hechos_automaticos(self):
        result = evaluate_fundamentals(self.project, self.user, evaluator=FakeEvaluator())
        self.assertEqual(result["criteria"]["GROWTH"]["status"], "PASS")
        self.assertEqual(self.project.criterion_assessments.get(criterion_code="SUPPLY").source_mode, "AUTOMATIC")
        self.assertEqual(self.project.criterion_assessments.get(criterion_code="GROWTH").source_mode, "AI_ASSISTED")

    def test_evaluacion_rechaza_evidencia_inventada(self):
        class InvalidEvaluator:
            def evaluate(self, payload, user):
                return {"criteria": {"GROWTH": {"status": "PASS", "evidence_ids": [999999]}}}

        with self.assertRaises(FundamentalEvaluationError):
            evaluate_fundamentals(self.project, self.user, evaluator=InvalidEvaluator())
