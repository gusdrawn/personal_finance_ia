from django.core.management.base import BaseCommand

from altcoins.services.providers.coingecko import CoinGeckoProvider
from altcoins.services.sync import sync_market


class Command(BaseCommand):
    help = "Crea el universo inicial estable del scanner (máximo 45 altcoins)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=45)

    def handle(self, *args, **options):
        run = sync_market(provider=CoinGeckoProvider(), bootstrap=True, limit=min(options["limit"], 45))
        self.stdout.write(self.style.SUCCESS(f"Universo sincronizado: {run.records_updated} snapshots y temáticas base."))
