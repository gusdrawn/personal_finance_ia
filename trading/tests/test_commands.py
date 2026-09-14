from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings

from trading.models import Instrument, TradingSyncRun
from trading.tasks import sync_trading_daily_task


class TradingSyncCommandTests(TestCase):
    @override_settings(TRADING_TWELVE_DATA_API_KEY="", TRADING_FMP_API_KEY="")
    @patch("trading.management.commands.sync_trading_data.sync_sec_instrument", return_value={"updated": 1, "statements": 3, "warnings": []})
    @patch("trading.management.commands.sync_trading_data.sync_symbol")
    def test_without_keys_uses_yahoo_and_sec_never_demo(self, sync_symbol_mock, sync_sec_mock):
        instrument = Instrument.objects.create(symbol="MCD", name="McDonald's", exchange="NYSE")
        sync_symbol_mock.return_value = instrument
        call_command("sync_trading_data", mode="full", stdout=StringIO(), stderr=StringIO())
        run = TradingSyncRun.objects.get()
        self.assertEqual(run.status, "SUCCESS")
        self.assertEqual(run.provider, "YAHOO_FINANCE+SEC_EDGAR")
        self.assertEqual(sync_symbol_mock.call_args.kwargs["provider"].name, "YAHOO_FINANCE")
        sync_sec_mock.assert_called_once()

    @override_settings(TRADING_DATA_MODE="HYBRID", TRADING_TWELVE_DATA_API_KEY="twelve", TRADING_FMP_API_KEY="fmp")
    @patch("trading.management.commands.sync_trading_data.sync_fmp_events", return_value=1)
    @patch("trading.management.commands.sync_trading_data.sync_fmp_instrument", return_value={"updated": 1, "statements": 3, "warnings": []})
    @patch("trading.management.commands.sync_trading_data.sync_sec_instrument", return_value={"updated": 1, "statements": 3, "warnings": []})
    @patch("trading.management.commands.sync_trading_data.sync_symbol")
    def test_full_real_sync_orchestrates_market_fundamentals_and_events(self, sync_symbol_mock, sync_sec_mock, sync_fmp_mock, sync_events_mock):
        instrument = Instrument.objects.create(symbol="MCD", name="McDonald's", exchange="NYSE")
        sync_symbol_mock.return_value = instrument
        call_command("sync_trading_data", mode="full", symbols="MCD", stdout=StringIO(), stderr=StringIO())
        run = TradingSyncRun.objects.get()
        self.assertEqual(run.status, "SUCCESS")
        self.assertEqual(run.provider, "TWELVE_DATA+SEC_EDGAR+FMP")
        sync_symbol_mock.assert_called_once()
        sync_sec_mock.assert_called_once()
        sync_fmp_mock.assert_called_once()
        sync_events_mock.assert_called_once()

    @override_settings(TRADING_DATA_MODE="LIVE")
    @patch("trading.tasks.call_command", create=True)
    def test_scheduled_task_uses_full_sync_in_live_mode(self, call_command_mock):
        result = sync_trading_daily_task.run()
        call_command_mock.assert_called_once_with("sync_trading_data", mode="full")
        self.assertEqual(result["operation"], "full")
