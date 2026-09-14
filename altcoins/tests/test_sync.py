from datetime import datetime, timezone
from decimal import Decimal

from django.test import TestCase

from altcoins.models import AltcoinMarketSnapshot, AltcoinProject, AltcoinSyncRun
from altcoins.services.providers.base import AssetCandidate, MarketObservation
from altcoins.services.sync import sync_market


class FakeMarketProvider:
    def fetch_universe(self, limit=100):
        return [AssetCandidate("demo", "Demo Coin", "DMC", 1, Decimal("100"))]

    def fetch_market(self, external_ids):
        return [MarketObservation("demo", datetime(2026, 8, 27, tzinfo=timezone.utc), price_usd=Decimal("2.5"), market_cap_usd=Decimal("100"), ath_usd=Decimal("10"), circulating_supply=Decimal("10"))]


class FailingMarketProvider(FakeMarketProvider):
    def fetch_universe(self, limit=100):
        raise RuntimeError("provider down")


class AltcoinSyncTest(TestCase):
    def test_sync_sin_universo_no_llama_al_proveedor(self):
        run = sync_market(provider=FakeMarketProvider(), bootstrap=False, limit=1)
        self.assertEqual(run.status, "PARTIAL")
        self.assertEqual(run.error_code, "EMPTY_UNIVERSE")

    def test_sync_mockeada_es_idempotente_para_mismo_snapshot(self):
        provider = FakeMarketProvider()
        first = sync_market(provider=provider, bootstrap=True, limit=1)
        second = sync_market(provider=provider, bootstrap=False, limit=1)
        self.assertEqual(first.status, "SUCCESS")
        self.assertEqual(second.status, "SUCCESS")
        self.assertEqual(AltcoinProject.objects.filter(in_universe=True).count(), 1)
        self.assertEqual(AltcoinMarketSnapshot.objects.count(), 1)
        self.assertEqual(AltcoinSyncRun.objects.filter(status="SUCCESS").count(), 2)

    def test_fallo_de_proveedor_conserva_auditoria(self):
        with self.assertRaises(RuntimeError):
            sync_market(provider=FailingMarketProvider(), bootstrap=True, limit=1)
        run = AltcoinSyncRun.objects.get()
        self.assertEqual(run.status, "FAILED")
        self.assertEqual(run.error_code, "RuntimeError")
