from decimal import Decimal

from django.test import TestCase

from altcoins.models import AltcoinProject, AltcoinTheme
from altcoins.services.providers.base import AssetCandidate
from altcoins.services.universe import bootstrap_universe


class AltcoinUniverseTest(TestCase):
    def test_bootstrap_excluye_btc_y_completa_hasta_limite_sin_reordenar(self):
        candidates = [AssetCandidate("btc", "Bitcoin", "btc", 1, Decimal("100")), AssetCandidate("usdt", "Tether", "usdt", 2, Decimal("90"))]
        candidates += [AssetCandidate(f"coin-{i}", f"Coin {i}", f"C{i}", i + 1, Decimal("10")) for i in range(50)]
        selected = bootstrap_universe(candidates, limit=45)
        self.assertEqual(AltcoinProject.objects.filter(in_universe=True).count(), 45)
        self.assertNotIn("btc", list(AltcoinProject.objects.filter(in_universe=True).values_list("symbol", flat=True)))
        self.assertEqual(selected[0].universe_position, 1)
        self.assertIsNotNone(selected[0].instrument_id)
        self.assertEqual(selected[0].instrument.asset_type, "CRYPTO")
        self.assertTrue(AltcoinTheme.objects.filter(slug="rwa-tokenizacion").exists())
