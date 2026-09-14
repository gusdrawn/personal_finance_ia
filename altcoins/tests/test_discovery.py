from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from altcoins.models import AltcoinCriterionAssessment, AltcoinDiscoveryCandidate, AltcoinResearchPacket, AltcoinProject, EvidenceRecord
from altcoins.services.discovery import DiscoveryError, build_research_packet, discover_by_query, persist_candidates, promote_candidate, research_candidate
from altcoins.services.providers.base import AssetCandidate
from altcoins.services.providers.robinhood import RobinhoodAvailabilityProvider
from altcoins.services.providers.web_search import BraveSearchProvider, WebSearchProviderError
from altcoins.services.providers.tokenomist import TokenomistProvider


class FakeCoinGecko:
    def search(self, query, limit=20):
        return [AssetCandidate("new-token", "New Token", "NEW", 420, None, "", {})]

    def fetch_details(self, external_id):
        return {
            "name": "New Token",
            "symbol": "new",
            "description": {"en": "Proyecto de prueba con documentación pública."},
            "links": {"homepage": ["https://project.example.com"], "twitter_screen_name": "newtoken"},
            "market_data": {
                "current_price": {"usd": 1},
                "market_cap": {"usd": 1000},
                "fully_diluted_valuation": {"usd": 1100},
                "total_volume": {"usd": 100},
                "ath": {"usd": 5},
                "ath_change_percentage": {"usd": -80},
                "circulating_supply": 950,
                "max_supply": 1000,
                "total_supply": 1000,
            },
            "community_data": {},
            "developer_data": {},
            "categories": ["decentralized finance"],
        }


class DiscoveryServiceTest(TestCase):
    def test_busqueda_no_se_mezcla_con_universo_hasta_promover(self):
        candidates = discover_by_query("new", coingecko_provider=FakeCoinGecko())
        self.assertEqual(len(candidates), 1)
        self.assertFalse(AltcoinProject.objects.exists())
        self.assertEqual(candidates[0].status, "NEW")
        project = promote_candidate(candidates[0])
        self.assertTrue(project.in_universe)
        self.assertEqual(project.universe_position, 1)
        self.assertEqual(candidates[0].refresh_from_db(), None)
        self.assertEqual(candidates[0].status, "PROMOTED")

    def test_dossier_normaliza_tokenomics_fuentes_y_aplica_hechos_deterministas(self):
        candidate = AltcoinDiscoveryCandidate.objects.create(
            source="COINGECKO_SEARCH", external_id="new-token", name="New Token", symbol="NEW", coingecko_id="new-token", website="https://project.example.com"
        )
        project = promote_candidate(candidate)

        def fetcher(url):
            return {"url": url, "content_type": "text/html", "body": b"<html><title>Docs</title><p>Uso real y transparente.</p></html>", "content_hash": ("a" if "coingecko" in url else "b") * 64}

        packet = research_candidate(candidate, coingecko_provider=FakeCoinGecko(), details_provider=FakeCoinGecko(), fetcher=fetcher)
        self.assertEqual(packet.status, "SUCCESS")
        self.assertGreaterEqual(packet.evidence_count, 2)
        self.assertEqual(packet.payload["tokenomics"]["fdv_to_market_cap"], "1.1000")
        self.assertEqual(AltcoinCriterionAssessment.objects.get(project=project, criterion_code="SUPPLY").status, "PASS")
        self.assertEqual(AltcoinCriterionAssessment.objects.get(project=project, criterion_code="ATH_DISCOUNT").status, "PASS")
        self.assertTrue(project.theme_links.filter(theme__slug="defi-perpetuos").exists())

    def test_robinhood_parser_no_duplica_tickers(self):
        rows = RobinhoodAvailabilityProvider.parse_catalog("<p>New Token (NEW)</p><p>New Token (NEW)</p><p>USD Coin (USDC)</p>")
        self.assertEqual([(item.name, item.symbol) for item in rows], [("New Token", "NEW"), ("USD Coin", "USDC")])

    def test_brave_sin_key_no_consulta(self):
        with self.assertRaises(WebSearchProviderError):
            BraveSearchProvider(session=Mock(), api_key="").search("depin")

    def test_no_promueve_resultado_web_sin_ticker(self):
        candidate = AltcoinDiscoveryCandidate.objects.create(source="WEB_SEARCH", external_id="https://example.com/project", name="Proyecto sin ticker", symbol="")
        with self.assertRaises(DiscoveryError):
            promote_candidate(candidate)

    def test_tokenomist_enriquece_supply_y_unlocks(self):
        session = Mock()
        token_response = Mock()
        token_response.json.return_value = {"status": True, "data": [{"id": "new", "name": "New Token", "symbol": "NEW", "maxSupply": 1000}]}
        token_response.raise_for_status.return_value = None
        unlock_response = Mock()
        unlock_response.json.return_value = {"status": True, "data": [{"unlockDate": "2026-09-01", "tokenName": "New Token"}]}
        unlock_response.raise_for_status.return_value = None
        session.get.side_effect = [token_response, unlock_response]
        result = TokenomistProvider(session=session, api_key="tokenomist-key").fetch_tokenomics("NEW", "New Token")
        self.assertEqual(result["maxSupply"], 1000)
        self.assertEqual(len(result["upcoming_unlock_events"]), 1)


class DiscoveryViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="discovery", password="secret")
        self.staff = User.objects.create_user(username="discovery-staff", password="secret", is_staff=True)

    def test_scanner_expone_cola_y_acciones_sin_comandos(self):
        AltcoinDiscoveryCandidate.objects.create(source="COINGECKO_SEARCH", external_id="candidate", name="Candidate", symbol="CAN")
        self.client.force_login(self.staff)
        response = self.client.get(reverse("altcoins:scanner"))
        self.assertContains(response, "Buscar proyectos nuevos")
        self.assertContains(response, "Investigar web + tokenomics")

    @patch("altcoins.views.discover_by_query")
    def test_solo_staff_puede_lanzar_busqueda(self, discover_mock):
        discover_mock.return_value = []
        self.client.force_login(self.user)
        response = self.client.post(reverse("altcoins:discover"), {"query": "depin", "source": "COINGECKO_SEARCH"})
        self.assertEqual(response.status_code, 302)
        discover_mock.assert_not_called()
        self.client.force_login(self.staff)
        response = self.client.post(reverse("altcoins:discover"), {"query": "depin", "source": "COINGECKO_SEARCH"})
        self.assertRedirects(response, reverse("altcoins:scanner"), fetch_redirect_response=False)
        discover_mock.assert_called_once()
