from datetime import date
from unittest.mock import Mock

from django.test import SimpleTestCase, override_settings

from trading.services.providers.fmp import FMPProvider
from trading.services.providers.sec import SECProvider
from trading.services.providers.twelve_data import ProviderError, TwelveDataProvider
from trading.services.providers.yahoo import YahooFinanceProvider


class TwelveDataProviderTests(SimpleTestCase):
    @override_settings(TRADING_TWELVE_DATA_API_KEY="test-key")
    def test_daily_bars_are_normalized_without_network(self):
        session = Mock()
        response = Mock()
        response.json.return_value = {"values": [{"datetime": "2026-08-26", "open": "10", "high": "11", "low": "9", "close": "10.5", "volume": "1000"}]}
        response.raise_for_status.return_value = None
        session.get.return_value = response
        bars = TwelveDataProvider(session=session).fetch_daily_bars("AAPL", start=date(2026, 8, 1))
        self.assertEqual(bars[0].session_date, date(2026, 8, 26))
        self.assertEqual(str(bars[0].close), "10.5")
        session.get.assert_called_once()

    def test_missing_key_fails_closed(self):
        with self.assertRaises(ProviderError):
            TwelveDataProvider(api_key="", session=Mock()).fetch_metrics("AAPL")


class FMPProviderTests(SimpleTestCase):
    def test_profile_uses_current_stable_endpoint_without_network(self):
        session = Mock()
        response = Mock()
        response.json.return_value = [{"symbol": "MCD", "companyName": "McDonald's Corporation"}]
        response.raise_for_status.return_value = None
        session.get.return_value = response
        profile = FMPProvider(api_key="test-key", session=session).fetch_profile("MCD")
        self.assertEqual(profile["symbol"], "MCD")
        url = session.get.call_args.args[0]
        params = session.get.call_args.kwargs["params"]
        self.assertEqual(url, "https://financialmodelingprep.com/stable/profile")
        self.assertEqual(params["symbol"], "MCD")
        self.assertEqual(params["apikey"], "test-key")

    def test_missing_fmp_key_fails_closed(self):
        with self.assertRaises(ProviderError):
            FMPProvider(api_key="", session=Mock()).fetch_profile("MCD")


class YahooFinanceProviderTests(SimpleTestCase):
    def test_daily_bars_and_crypto_alias_are_normalized_without_network(self):
        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"chart": {"result": [{"timestamp": [1787702400], "meta": {"regularMarketPrice": 100}, "indicators": {"quote": [{"open": [98], "high": [102], "low": [97], "close": [100], "volume": [1234]}], "adjclose": [{"adjclose": [100]}]}}], "error": None}}
        session.get.return_value = response
        provider = YahooFinanceProvider(session=session)
        bars = provider.fetch_daily_bars("BTCUSD")
        self.assertEqual(str(bars[0].close), "100")
        self.assertIn("BTC-USD", session.get.call_args.args[0])
        self.assertEqual(provider.fetch_metrics("BTCUSD")["price"], 100)

    def test_invalid_payload_fails_closed(self):
        session = Mock()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"chart": {"result": None, "error": {"description": "No encontrado"}}}
        session.get.return_value = response
        with self.assertRaises(ProviderError):
            YahooFinanceProvider(session=session).fetch_daily_bars("NOPE")


class SECProviderTests(SimpleTestCase):
    def test_ticker_is_resolved_and_companyfacts_requested_without_key(self):
        session = Mock()
        ticker_response = Mock()
        ticker_response.raise_for_status.return_value = None
        ticker_response.json.return_value = {"0": {"ticker": "MCD", "cik_str": 63908, "title": "McDonald's Corporation"}}
        facts_response = Mock()
        facts_response.raise_for_status.return_value = None
        facts_response.json.return_value = {"facts": {}}
        session.get.side_effect = [ticker_response, facts_response]
        payload = SECProvider(session=session).fetch_company_facts("MCD")
        self.assertEqual(payload["resolved_company"]["cik"], "0000063908")
        self.assertIn("CIK0000063908.json", session.get.call_args_list[1].args[0])
        self.assertIn("User-Agent", session.get.call_args_list[1].kwargs["headers"])
