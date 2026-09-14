from __future__ import annotations

from django.utils import timezone

from altcoins.models import AltcoinMarketSnapshot, AltcoinProject, AltcoinSyncRun
from .providers.coingecko import CoinGeckoProvider
from .universe import bootstrap_universe


def sync_market(provider=None, bootstrap=False, limit=100):
    provider = provider or CoinGeckoProvider()
    run = AltcoinSyncRun.objects.create(provider="COINGECKO", operation="market")
    try:
        if bootstrap or not AltcoinProject.objects.filter(in_universe=True).exists():
            candidates = provider.fetch_universe(limit=max(limit, 100))
            projects = bootstrap_universe(candidates, limit=min(limit, 45)) if bootstrap else []
        else:
            projects = list(AltcoinProject.objects.filter(in_universe=True))
        if not projects:
            run.status = "PARTIAL"
            run.error_code = "EMPTY_UNIVERSE"
            run.error_message = "Inicializa el scanner antes de sincronizar el mercado."
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "error_code", "error_message", "finished_at"])
            return run
        ids = [project.coingecko_id for project in projects if project.coingecko_id]
        observations = provider.fetch_market(ids)
        by_id = {project.coingecko_id: project for project in projects}
        from django.db import transaction
        with transaction.atomic():
            for observation in observations:
                project = by_id.get(observation.external_id)
                if not project:
                    continue
                drawdown = None
                if observation.ath_usd and observation.price_usd and observation.ath_usd > 0:
                    drawdown = (1 - observation.price_usd / observation.ath_usd) * 100
                AltcoinMarketSnapshot.objects.update_or_create(
                    project=project, observed_at=observation.observed_at,
                    defaults={"price_usd": observation.price_usd, "market_cap_usd": observation.market_cap_usd, "fdv_usd": observation.fdv_usd, "volume_24h_usd": observation.volume_24h_usd, "circulating_supply": observation.circulating_supply, "total_supply": observation.total_supply, "max_supply": observation.max_supply, "ath_usd": observation.ath_usd, "ath_drawdown_pct": drawdown, "market_cap_rank": observation.market_cap_rank, "raw_payload": observation.raw},
                )
        run.status = "SUCCESS"
        run.records_received = len(observations)
        run.records_updated = len(observations)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "records_received", "records_updated", "finished_at"])
        return run
    except Exception as exc:
        run.status = "FAILED"
        run.error_code = exc.__class__.__name__
        run.error_message = str(exc)[:2000]
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_code", "error_message", "finished_at"])
        raise
