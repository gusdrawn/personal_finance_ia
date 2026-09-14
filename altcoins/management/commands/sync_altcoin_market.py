from django.core.management.base import BaseCommand

from altcoins.services.sync import sync_market


class Command(BaseCommand):
    help = "Actualiza snapshots de mercado para el universo de altcoins."

    def handle(self, *args, **options):
        run = sync_market()
        self.stdout.write(self.style.SUCCESS(f"Mercado actualizado: {run.records_updated} registros."))
