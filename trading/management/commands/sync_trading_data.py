from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from trading.models import Instrument, TradingSyncRun
from trading.services.live import sync_fmp_events, sync_fmp_instrument
from trading.services.providers.fmp import FMPProvider
from trading.services.providers.sec import SECProvider
from trading.services.providers.twelve_data import ProviderError, TwelveDataProvider
from trading.services.providers.yahoo import YahooFinanceProvider
from trading.services.sec import sync_sec_instrument
from trading.services.sync import sync_symbol


CORE_SYMBOLS = ("MCD", "SPY", "QQQ", "GLD", "USO", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "XOM", "CVX", "JPM", "V", "BTCUSD", "ETHUSD")


class Command(BaseCommand):
    help = "Actualiza investigación de mercado con precios públicos y fundamentales SEC; nunca carga datos demo."

    def add_arguments(self, parser):
        parser.add_argument("--mode", choices=["catalog", "daily", "fundamentals", "events", "full"], default="daily")
        parser.add_argument("--symbols", default="")

    def handle(self, *args, **options):
        mode = options["mode"]
        market_provider = TwelveDataProvider() if settings.TRADING_TWELVE_DATA_API_KEY else YahooFinanceProvider()
        provider_names = [market_provider.name, "SEC_EDGAR"]
        if settings.TRADING_FMP_API_KEY:
            provider_names.append("FMP")
        run = TradingSyncRun.objects.create(provider="+".join(provider_names), operation=mode)
        symbols = [item.strip().upper() for item in options["symbols"].split(",") if item.strip()]
        if symbols:
            targets = symbols
        else:
            targets = list(
                Instrument.objects.filter(active=True)
                .filter(Q(symbol__in=CORE_SYMBOLS) | Q(source__in=["DEMO", "YAHOO_FINANCE", "TWELVE_DATA", "SEC_EDGAR", "FMP"]))
                .order_by("symbol").values_list("symbol", flat=True).distinct()
            ) or list(CORE_SYMBOLS)
        errors = []
        processed = 0

        if mode in {"catalog", "daily", "full"}:
            for symbol in targets:
                try:
                    sync_symbol(symbol, provider=market_provider)
                    processed += 1
                except (ProviderError, ValueError) as exc:
                    errors.append(f"{symbol}/mercado: {exc}")

        if mode in {"fundamentals", "full"}:
            instruments = Instrument.objects.filter(active=True, symbol__in=targets, asset_type="STOCK")
            sec_provider = SECProvider()
            for instrument in instruments:
                try:
                    result = sync_sec_instrument(instrument, provider=sec_provider)
                    processed += result["updated"]
                    errors.extend(f"{instrument.symbol}/SEC: {warning}" for warning in result["warnings"])
                except ProviderError as exc:
                    errors.append(f"{instrument.symbol}/SEC: {exc}")
            if settings.TRADING_FMP_API_KEY:
                fundamental_provider = FMPProvider()
                for instrument in instruments:
                    try:
                        result = sync_fmp_instrument(instrument, provider=fundamental_provider)
                        processed += result["updated"]
                        errors.extend(f"{instrument.symbol}/{warning}" for warning in result["warnings"])
                    except ProviderError as exc:
                        errors.append(f"{instrument.symbol}/fundamentals: {exc}")

        if mode in {"events", "full"}:
            if settings.TRADING_FMP_API_KEY:
                try:
                    start = timezone.localdate()
                    processed += sync_fmp_events(FMPProvider(), start=start, end=start + timedelta(days=45))
                except ProviderError as exc:
                    errors.append(f"Calendario: {exc}")
        run.status = "PARTIAL" if errors and processed else "FAILED" if errors else "SUCCESS"
        run.error_message = " | ".join(errors)[:4000]
        run.records_received = len(targets)
        run.records_updated = processed
        self._finish(run)
        if run.status == "FAILED":
            self.stderr.write(self.style.ERROR("No se pudo actualizar la información actual. Revisa el detalle de la sincronización."))
        elif run.status == "PARTIAL":
            self.stdout.write(self.style.WARNING(f"Actualización parcial: {processed} bloques reales guardados."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Actualización completada: {processed} bloques reales guardados."))

    @staticmethod
    def _finish(run):
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "records_received", "records_created", "records_updated", "error_message", "finished_at"])
