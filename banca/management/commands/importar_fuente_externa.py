from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError

from banca.services.fuentes_externas import importar_archivo_externo


class Command(BaseCommand):
    help = "Importa un informe Houm o historial Neat y ejecuta conciliación idempotente."

    def add_arguments(self, parser):
        parser.add_argument("fuente", choices=["houm", "neat"])
        parser.add_argument("archivo")
        parser.add_argument("--usuario", required=True, help="Nombre de usuario propietario")

    def handle(self, *args, **options):
        path = Path(options["archivo"]).expanduser().resolve()
        if not path.is_file():
            raise CommandError(f"No existe el archivo: {path}")
        try:
            user = get_user_model().objects.get(username=options["usuario"])
        except get_user_model().DoesNotExist as exc:
            raise CommandError("Usuario no encontrado.") from exc
        uploaded = SimpleUploadedFile(path.name, path.read_bytes())
        record, duplicate = importar_archivo_externo(user, options["fuente"], uploaded)
        if record.estado == "ERROR":
            raise CommandError(record.mensaje)
        self.stdout.write(self.style.SUCCESS(
            f"{'Ya importado' if duplicate else 'Procesado'}: {record.elementos_detectados} elemento(s), "
            f"{record.nuevos} nuevo(s), {record.actualizados} actualizado(s)."
        ))
