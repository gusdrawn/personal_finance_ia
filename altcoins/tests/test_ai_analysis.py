from django.contrib.auth.models import User
from django.test import TestCase

from altcoins.models import AltcoinProject, EvidenceRecord, InstitutionalEntity, ProjectEntityRelationship
from altcoins.services.ai_analysis import AltcoinAIError, analyze_project


class FakeAnalyst:
    def __init__(self, result):
        self.result = result

    def analyze(self, payload, user):
        return self.result


class AltcoinAIAnalysisTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ai_user", password="secret")
        self.project = AltcoinProject.objects.create(name="AI Demo", symbol="AID", slug="ai-demo", in_universe=True)
        entity = InstitutionalEntity.objects.create(canonical_name="Fondo IA", normalized_name="fondo ia", slug="fondo-ia", entity_type="VC_FUND")
        evidence = EvidenceRecord.objects.create(source_type="PROJECT_OFFICIAL", source_url="https://example.com/round", title="Ronda", excerpt="Ronda anunciada", content_hash="a" * 64, reliability_tier="A_PRIMARY")
        relation = ProjectEntityRelationship.objects.create(project=self.project, entity=entity, relationship_type="TOKEN_INVESTOR", status="VERIFIED")
        relation.evidence.add(evidence)
        self.entity_id = entity.id
        self.evidence_id = evidence.id

    def test_analisis_usa_solo_entidades_y_evidencias_permitidas_y_cachea(self):
        result = {"summary": "Resumen", "supporting_entities": [{"entity_id": self.entity_id, "evidence_ids": [self.evidence_id]}]}
        analyst = FakeAnalyst(result)
        first = analyze_project(self.project, self.user, analyst=analyst)
        second = analyze_project(self.project, self.user, analyst=FakeAnalyst({"summary": "No debería llamarse", "supporting_entities": []}))
        self.assertEqual(first["summary"], "Resumen")
        self.assertEqual(second["summary"], "Resumen")

    def test_rechaza_evidencia_inventada_por_la_ia(self):
        with self.assertRaises(AltcoinAIError):
            analyze_project(self.project, self.user, analyst=FakeAnalyst({"supporting_entities": [{"entity_id": self.entity_id, "evidence_ids": [999999]}]}))
