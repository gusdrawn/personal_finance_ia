from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from trading.models import AnalystSnapshot, DailyBar, FinancialStatementSnapshot, Instrument, InstrumentMetricSnapshot, InstrumentUserState
from trading.services.demo import seed_demo_data
from trading.services.providers.base import AssetCandidate
from trading.services.providers.twelve_data import ProviderError


class TradingViewsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="researcher", password="safe-pass-123")
        cls.staff = get_user_model().objects.create_user(username="trading-admin", password="safe-pass-123", is_staff=True)
        seed_demo_data(days=60)
        DailyBar.objects.update(source="TEST_MARKET")
        InstrumentMetricSnapshot.objects.update(source="TEST_FUNDAMENTALS")
        FinancialStatementSnapshot.objects.update(source="TEST_FUNDAMENTALS")
        AnalystSnapshot.objects.update(provider="TEST_FUNDAMENTALS")
        Instrument.objects.update(source="TEST_MARKET")
        cls.instrument = Instrument.objects.get(symbol="AAPL")

    def setUp(self):
        self.client.force_login(self.user)

    def test_research_and_detail_render_with_current_data(self):
        response = self.client.get(reverse("trading:research"), {"theme": "ia-y-semiconductores"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Research Center")
        response = self.client.get(reverse("trading:instrument_detail", args=[self.instrument.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "QuantPro")
        self.assertContains(response, "Este portal no se conecta a brokers")

    def test_chart_contract_and_state_is_user_scoped(self):
        response = self.client.get(reverse("trading:api_chart", args=[self.instrument.pk]), {"chart": "performance"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["chart"], "performance")
        self.assertIn("series", payload)
        response = self.client.post(reverse("trading:api_state", args=[self.instrument.pk]), {"action": "discard"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(InstrumentUserState.objects.get(user=self.user, instrument=self.instrument).discarded)

    def test_public_api_aliases_return_json(self):
        response = self.client.get(reverse("trading:api_chart_named", args=[self.instrument.pk, "drawdown"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["chart"], "drawdown")
        response = self.client.post(reverse("trading:api_watch", args=[self.instrument.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["watched"])

    def test_search_mcd_opens_complete_report(self):
        mcd = Instrument.objects.get(symbol="MCD")
        response = self.client.get(reverse("trading:research"), {"q": "MCD"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "McDonald&#x27;s Corporation")
        response = self.client.get(reverse("trading:instrument_detail", args=[mcd.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ingresos anuales")
        self.assertContains(response, "P/E histórico al cierre anual")
        self.assertContains(response, "EPS reportado y estimaciones disponibles")
        self.assertContains(response, "TEST_FUNDAMENTALS")

    def test_mcd_report_does_not_leak_demo_snapshots(self):
        mcd = Instrument.objects.get(symbol="MCD")
        mcd.metric_snapshots.update(source="DEMO")
        mcd.daily_bars.update(source="DEMO")
        mcd.financial_statements.update(source="DEMO")
        response = self.client.get(reverse("trading:instrument_detail", args=[mcd.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No hay snapshots reales sincronizados")
        self.assertContains(response, "Fuentes SIN_DATOS")
        self.assertContains(response, "Sin estados financieros reales sincronizados")
        self.assertNotContains(response, "$211.6B")
        self.assertNotContains(response, "Dataset demostrativo")

    def test_search_api_marks_mcd_as_local(self):
        response = self.client.get(reverse("trading:api_search"), {"q": "MCD"})
        self.assertEqual(response.status_code, 200)
        result = response.json()["results"][0]
        self.assertEqual(result["symbol"], "MCD")
        self.assertTrue(result["local"])

    def test_operations_panel_is_visible_only_to_staff(self):
        response = self.client.get(reverse("trading:dashboard"))
        self.assertNotContains(response, "Fuentes y actualización")
        self.client.force_login(self.staff)
        response = self.client.get(reverse("trading:dashboard"))
        self.assertContains(response, "Fuentes y actualización")
        self.assertNotContains(response, "Cargar datos demo")
        self.assertContains(response, "DATOS ACTUALES · SIN DEMO")
        self.assertContains(response, "Actualizar información actual")
        self.assertContains(response, "Actualizar información ahora")
        self.assertContains(response, "Vincular proyectos")

    @patch("trading.views.call_command")
    def test_seed_demo_operation_is_not_available(self, call_command_mock):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("trading:run_operation", args=["seed_demo"]))
        self.assertRedirects(response, reverse("trading:dashboard"), fetch_redirect_response=False)
        call_command_mock.assert_not_called()

    @patch("trading.views.call_command")
    def test_regular_user_cannot_run_global_operation(self, call_command_mock):
        response = self.client.post(reverse("trading:run_operation", args=["sync_daily"]))
        self.assertEqual(response.status_code, 302)
        call_command_mock.assert_not_called()

    @patch("trading.views.call_command")
    def test_staff_buttons_dispatch_daily_sync_and_altcoin_link(self, call_command_mock):
        self.client.force_login(self.staff)
        for action in ("sync_daily", "link_altcoins"):
            with self.subTest(action=action):
                response = self.client.post(reverse("trading:run_operation", args=[action]))
                self.assertRedirects(response, reverse("trading:dashboard"), fetch_redirect_response=False)
        self.assertEqual(call_command_mock.call_args_list[0].args[0], "sync_trading_data")
        self.assertEqual(call_command_mock.call_args_list[0].kwargs["mode"], "full")
        self.assertEqual(call_command_mock.call_args_list[1].args[0], "link_altcoins_to_trading")

    @override_settings(TRADING_DATA_MODE="HYBRID", TRADING_TWELVE_DATA_API_KEY="test-key")
    @patch("trading.views.YahooFinanceProvider")
    def test_search_api_discovers_external_symbol_without_persisting(self, provider_class):
        provider_class.return_value.search_instruments.return_value = [AssetCandidate(symbol="KO", name="Coca-Cola", exchange="NYSE")]
        response = self.client.get(reverse("trading:api_search"), {"q": "KO"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["results"][0]["local"])
        self.assertFalse(Instrument.objects.filter(symbol="KO").exists())

    @override_settings(TRADING_DATA_MODE="HYBRID", TRADING_TWELVE_DATA_API_KEY="test-key")
    def test_research_offers_import_for_unknown_valid_symbol(self):
        response = self.client.get(reverse("trading:research"), {"q": "KO"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Buscar y guardar datos actuales de KO")

    @override_settings(TRADING_DATA_MODE="HYBRID", TRADING_TWELVE_DATA_API_KEY="test-key")
    @patch("trading.views.sync_symbol")
    @patch("trading.views.sync_sec_instrument")
    def test_import_symbol_returns_detail_url(self, sync_sec_mock, sync_symbol_mock):
        sync_symbol_mock.return_value = self.instrument
        response = self.client.post(reverse("trading:api_import_symbol"), {"symbol": "AAPL"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["id"], self.instrument.id)
        sync_sec_mock.assert_called_once_with(self.instrument)

    @override_settings(TRADING_TWELVE_DATA_API_KEY="test-key")
    @patch("trading.views.sync_symbol")
    @patch("trading.views.sync_sec_instrument", side_effect=ProviderError("sin CIK"))
    def test_import_keeps_market_data_when_sec_does_not_cover_company(self, sync_sec_mock, sync_symbol_mock):
        sync_symbol_mock.return_value = self.instrument
        response = self.client.post(reverse("trading:api_import_symbol"), {"symbol": "AAPL"})
        self.assertEqual(response.status_code, 201)
        self.assertIn("SEC no entregó", response.json()["warnings"][0])

    def test_research_assistant_returns_mcd_with_source_and_date(self):
        response = self.client.post(reverse("trading:api_ai_research"), {"prompt": "analiza MCD"})
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertEqual(result["assets"][0]["symbol"], "MCD")
        self.assertEqual(result["assets"][0]["source"], "TEST_MARKET")
        self.assertTrue(result["assets"][0]["as_of"])

    def test_research_assistant_never_uses_demo(self):
        mcd = Instrument.objects.get(symbol="MCD")
        mcd.daily_bars.update(source="DEMO")
        response = self.client.post(reverse("trading:api_ai_research"), {"prompt": "analiza MCD"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["assets"], [])
        self.assertIn("No usaré datos demo", response.json()["result"]["summary"])
