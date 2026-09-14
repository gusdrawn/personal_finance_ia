from __future__ import annotations

from datetime import date

from django.db import transaction
from django.utils import timezone

from trading.models import DailyBar, Instrument, InstrumentMetricSnapshot
from .providers.twelve_data import TwelveDataProvider


def sync_instrument_daily(instrument: Instrument, provider=None, start: date | None = None, end: date | None = None) -> int:
    provider = provider or TwelveDataProvider()
    bars = provider.fetch_daily_bars(instrument.symbol, start=start, end=end)
    with transaction.atomic():
        for bar in bars:
            DailyBar.objects.update_or_create(instrument=instrument, session_date=bar.session_date, defaults={"open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close, "adjusted_close": bar.adjusted_close, "volume": bar.volume, "source": provider.name})
    return len(bars)


def sync_symbol(symbol: str, provider=None) -> Instrument:
    provider = provider or TwelveDataProvider()
    normalized_symbol = symbol.strip().upper()
    instrument = Instrument.objects.filter(symbol__iexact=normalized_symbol).first()
    if not instrument:
        candidates = provider.search_instruments(normalized_symbol)
        candidate = next((item for item in candidates if item.symbol.upper() == normalized_symbol), candidates[0] if candidates else None)
        if not candidate:
            raise ValueError(f"No se encontró {normalized_symbol}")
        instrument = Instrument.objects.create(symbol=candidate.symbol, name=candidate.name, asset_type=candidate.asset_type, exchange=candidate.exchange, currency=candidate.currency, sector=candidate.sector, industry=candidate.industry, source=provider.name)
    sync_instrument_daily(instrument, provider=provider)
    metrics = provider.fetch_metrics(instrument.symbol)
    if metrics:
        profile_updates = {
            "name": metrics.get("long_name"),
            "exchange": metrics.get("exchange"),
            "currency": metrics.get("currency"),
        }
        changed_fields = []
        for field, value in profile_updates.items():
            if value and getattr(instrument, field) != value:
                setattr(instrument, field, value)
                changed_fields.append(field)
        if changed_fields:
            instrument.save(update_fields=[*changed_fields, "updated_at"])
        market_cap = metrics.get("market_cap") or metrics.get("marketCap") or metrics.get("market_capitalization")
        InstrumentMetricSnapshot.objects.create(
            instrument=instrument,
            observed_at=timezone.now(),
            market_cap=market_cap,
            metrics={**metrics, "data_quality_note": f"Cotización e histórico sincronizados desde {provider.name}; fundamentales sujetos a un proveedor adicional."},
            source=provider.name,
        )
    if instrument.source != provider.name:
        instrument.source = provider.name
        instrument.save(update_fields=["source", "updated_at"])
    return instrument
