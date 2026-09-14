from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from altcoins.models import AltcoinProject, AltcoinScoreSnapshot, EvidenceRecord, InstitutionalEntity, ProjectEntityRelationship


class AltcoinViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="viewer", password="secret")
        self.other = User.objects.create_user(username="other", password="secret")
        self.staff = User.objects.create_user(username="staff", password="secret", is_staff=True)
        self.project = AltcoinProject.objects.create(name="Render Demo", symbol="RNDR", slug="render-demo", in_universe=True)
        self.entity = InstitutionalEntity.objects.create(canonical_name="Fondo Demo", normalized_name="fondo demo", slug="fondo-demo", entity_type="VC_FUND")
        ProjectEntityRelationship.objects.create(project=self.project, entity=self.entity, relationship_type="TOKEN_INVESTOR", status="VERIFIED")

    def test_scanner_requiere_login_y_muestra_relacion_verificada(self):
        response = self.client.get(reverse("altcoins:scanner"))
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.user)
        response = self.client.get(reverse("altcoins:scanner"))
        self.assertContains(response, "Render Demo")
        self.assertContains(response, "Fondo Demo")
        self.assertNotContains(response, "Centro de operaciones")

    def test_scanner_muestra_operaciones_solo_a_staff(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("altcoins:scanner"))
        self.assertContains(response, "Centro de operaciones")
        self.assertContains(response, "Actualizar precios")
        self.assertContains(response, "Registrar sponsor, dueño, inversor o institución")

    @patch("altcoins.views.sync_market")
    def test_staff_puede_inicializar_desde_boton(self, sync_market_mock):
        sync_market_mock.return_value = SimpleNamespace(status="SUCCESS", records_updated=1, error_message="")
        self.client.force_login(self.staff)
        response = self.client.post(reverse("altcoins:run_operation", args=["bootstrap"]))
        self.assertRedirects(response, reverse("altcoins:scanner"), fetch_redirect_response=False)
        sync_market_mock.assert_called_once()
        self.assertTrue(sync_market_mock.call_args.kwargs["bootstrap"])
        self.assertEqual(sync_market_mock.call_args.kwargs["limit"], 45)

    @patch("altcoins.views.sync_market")
    def test_usuario_normal_no_puede_ejecutar_operaciones_globales(self, sync_market_mock):
        self.client.force_login(self.user)
        response = self.client.post(reverse("altcoins:run_operation", args=["market"]))
        self.assertEqual(response.status_code, 302)
        sync_market_mock.assert_not_called()

    def test_boton_scores_crea_snapshots_auditables(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("altcoins:run_operation", args=["scores"]))
        self.assertRedirects(response, reverse("altcoins:scanner"), fetch_redirect_response=False)
        self.assertEqual(AltcoinScoreSnapshot.objects.filter(project=self.project).count(), 1)
        self.assertEqual(self.project.institutional_assessments.count(), 1)

    @patch("altcoins.views.get_coinmarketcap_api_key", return_value="")
    @patch("altcoins.views.sync_coinmarketcap_metadata")
    def test_metadata_sin_api_key_no_intenta_conexion(self, metadata_mock, _key_mock):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("altcoins:run_operation", args=["metadata"]))
        self.assertRedirects(response, reverse("altcoins:scanner"), fetch_redirect_response=False)
        metadata_mock.assert_not_called()

    def test_staff_registra_evidencia_como_candidata(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("altcoins:register_institutional_evidence"), {
            "project": self.project.pk,
            "entity_name": "Banco Institucional",
            "entity_type": "BANK",
            "relationship_type": "STRATEGIC_BACKER",
            "source_url": "https://example.com/anuncio",
            "title": "Anuncio oficial",
            "excerpt": "El banco respalda el proyecto.",
            "reliability_tier": "A_PRIMARY",
        })
        self.assertRedirects(response, reverse("altcoins:scanner"), fetch_redirect_response=False)
        relation = ProjectEntityRelationship.objects.get(entity__normalized_name="banco institucional")
        self.assertEqual(relation.status, "CANDIDATE")
        self.assertEqual(relation.created_by, self.staff)
        self.assertEqual(EvidenceRecord.objects.filter(project_relationships=relation).count(), 1)

    @patch("altcoins.views.register_relationship_candidate")
    def test_evidencia_invalida_no_crea_relacion(self, register_mock):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("altcoins:register_institutional_evidence"), {
            "project": self.project.pk,
            "entity_name": "Entidad",
            "entity_type": "UNKNOWN",
            "relationship_type": "ADVISOR",
            "source_url": "no-es-url",
            "reliability_tier": "D_UNVERIFIED",
        })
        self.assertRedirects(response, reverse("altcoins:scanner"), fetch_redirect_response=False)
        register_mock.assert_not_called()

    def test_detalle_y_explorador_institucional_renderizan(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("altcoins:project_detail", args=[self.project.slug]))
        self.assertContains(response, "Huella institucional")
        response = self.client.get(reverse("altcoins:institution_detail", args=[self.entity.slug]))
        self.assertContains(response, "Render Demo")

    def test_watchlist_no_se_comparte(self):
        self.client.force_login(self.user)
        self.client.post(reverse("altcoins:toggle_watchlist", args=[self.project.slug]))
        self.assertEqual(self.project.watchers.filter(user=self.user).count(), 1)
        self.assertEqual(self.project.watchers.filter(user=self.other).count(), 0)

    def test_override_personal_se_aisla_y_redirige(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("altcoins:set_criterion_override", args=[self.project.slug]), {"criterion_code": "SUPPLY", "status": "FAIL", "rationale": "unlock"})
        self.assertRedirects(response, reverse("altcoins:project_detail", args=[self.project.slug]), fetch_redirect_response=False)
        self.assertEqual(self.project.user_overrides.get(user=self.user).status, "FAIL")

    def test_analisis_ia_sin_configuracion_no_rompe_la_pagina(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("altcoins:generate_ai_analysis", args=[self.project.slug]))
        self.assertRedirects(response, reverse("altcoins:project_detail", args=[self.project.slug]), fetch_redirect_response=False)
        self.assertEqual(response.wsgi_request._messages._queued_messages[0].level_tag, "error")
