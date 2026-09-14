from __future__ import annotations

try:
    from celery import shared_task
except Exception:  # Celery opcional en desarrollo
    def shared_task(*args, **kwargs):
        def decorator(fn):
            return fn
        return decorator if not (args and callable(args[0])) else args[0]


@shared_task
def sync_altcoin_market_task():
    from altcoins.services.sync import sync_market
    run = sync_market()
    return {"status": run.status, "records_updated": run.records_updated}


@shared_task
def sync_altcoin_metadata_task():
    from altcoins.services.metadata import sync_coinmarketcap_metadata
    from altcoins.services.providers.coinmarketcap import CoinMarketCapProvider
    return sync_coinmarketcap_metadata(CoinMarketCapProvider())
