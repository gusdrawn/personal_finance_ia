from django.core.management.base import BaseCommand

from altcoins.services.metadata import sync_coinmarketcap_metadata
from altcoins.services.providers.coinmarketcap import CoinMarketCapProvider


class Command(BaseCommand):
    help = "Cruza metadata de CoinMarketCap con el universo existente usando nombre exacto."

    def handle(self, *args, **options):
        result = sync_coinmarketcap_metadata(CoinMarketCapProvider())
        self.stdout.write(self.style.SUCCESS(f"Metadata CMC: {result['matched']} vinculados; {result['rejected']} pendientes de revisión."))
