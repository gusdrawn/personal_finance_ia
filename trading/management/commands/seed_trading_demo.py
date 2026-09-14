from django.core.management.base import BaseCommand

from trading.services.demo import seed_demo_data


class Command(BaseCommand):
    help = "Carga un universo sintético determinista para probar el Trading Research Center."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=320)

    def handle(self, *args, **options):
        result = seed_demo_data(days=max(30, min(options["days"], 2000)))
        self.stdout.write(self.style.SUCCESS(f"Trading demo listo: {result['instruments']} instrumentos, {result['bars']} barras y {result['events']} eventos."))
