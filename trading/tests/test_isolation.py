from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from trading.models import AIResearchRun, Instrument, InstrumentUserState
from trading.services.demo import seed_demo_data


class TradingIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.one = get_user_model().objects.create_user(username="one", password="safe-pass-123")
        cls.two = get_user_model().objects.create_user(username="two", password="safe-pass-123")
        seed_demo_data(days=35)
        cls.instrument = Instrument.objects.get(symbol="MSFT")

    def test_watchlist_and_ai_runs_are_isolated(self):
        self.client.force_login(self.one)
        self.client.post(reverse("trading:api_state", args=[self.instrument.pk]), {"action": "watch"})
        self.client.post(reverse("trading:api_ai_research"), {"prompt": "busca empresas de IA con QuantPro"})
        self.client.force_login(self.two)
        self.assertNotContains(self.client.get(reverse("trading:watchlist")), self.instrument.symbol)
        self.assertEqual(AIResearchRun.objects.filter(user=self.two).count(), 0)
        self.assertEqual(InstrumentUserState.objects.filter(user=self.one, instrument=self.instrument).count(), 1)
