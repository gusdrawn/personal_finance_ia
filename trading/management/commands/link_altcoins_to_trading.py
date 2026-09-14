from django.core.management.base import BaseCommand

from altcoins.models import AltcoinProject
from trading.models import Instrument


class Command(BaseCommand):
    help = "Vincula proyectos altcoin existentes a una ficha genérica trading.Instrument."

    def handle(self, *args, **options):
        linked = 0
        for project in AltcoinProject.objects.filter(instrument__isnull=True):
            instrument, _ = Instrument.objects.get_or_create(symbol=f"{project.symbol.upper()}USD", exchange="CRYPTO", defaults={"name": project.name, "asset_type": "CRYPTO", "currency": "USD", "source": "COINGECKO"})
            project.instrument = instrument
            project.save(update_fields=["instrument", "updated_at"])
            linked += 1
        self.stdout.write(self.style.SUCCESS(f"{linked} proyectos vinculados a Trading Research Center."))
