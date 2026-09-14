from django.test import TestCase

from altcoins.models import AltcoinExternalId, AltcoinProject
from altcoins.services.metadata import sync_coinmarketcap_metadata
from altcoins.services.providers.base import AssetCandidate


class FakeCMCProvider:
    def fetch_universe(self, limit=5000):
        return [AssetCandidate("123", "Coin Exacta", "EXA"), AssetCandidate("456", "Nombre Dudoso", "DUD")]


class AltcoinMetadataTest(TestCase):
    def test_solo_vincula_nombre_exacto_y_unico(self):
        AltcoinProject.objects.create(name="Coin Exacta", symbol="EXA", slug="coin-exacta", in_universe=True)
        AltcoinProject.objects.create(name="Nombre Dudoso", symbol="DUD", slug="nombre-dudoso-1", in_universe=True)
        AltcoinProject.objects.create(name="Nombre Dudoso", symbol="DUD2", slug="nombre-dudoso-2", in_universe=True)
        result = sync_coinmarketcap_metadata(FakeCMCProvider())
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["rejected"], 1)
        self.assertTrue(AltcoinExternalId.objects.filter(provider="CMC", external_id="123", verified=True).exists())
