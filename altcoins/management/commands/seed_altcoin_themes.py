from django.core.management.base import BaseCommand

from altcoins.services.universe import ensure_default_themes


class Command(BaseCommand):
    help = "Crea las temáticas iniciales del Altcoin Scanner."

    def handle(self, *args, **options):
        ensure_default_themes()
        self.stdout.write(self.style.SUCCESS("Temáticas de altcoins listas."))
