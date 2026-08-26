from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from banca.models import ApplePayApiKey


class Command(BaseCommand):
    help = "Genera una API key para un Atajo de Apple Pay y muestra el secreto una sola vez."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--name", default="Atajo iPhone")

    def handle(self, *args, **options):
        username = options["username"]
        user = get_user_model().objects.filter(username=username).first()
        if not user:
            raise CommandError(f"No existe el usuario «{username}».")
        key, secreto = ApplePayApiKey.crear(user, nombre=options["name"])
        self.stdout.write(self.style.SUCCESS(f"API key creada ({key.prefijo}…):"))
        self.stdout.write(secreto)
        self.stdout.write(
            self.style.WARNING("Guárdala ahora en el iPhone: no volverá a mostrarse.")
        )
