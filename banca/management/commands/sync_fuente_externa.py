from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from banca.models import CredencialBanco
from banca.services.fuentes_externas import conciliar_eventos, probar_conexion, run_external_sync


class Command(BaseCommand):
    help = "Sincroniza, prueba o reintenta la conciliación de Houm/Neat."

    def add_arguments(self, parser):
        parser.add_argument("fuente", choices=["houm", "neat", "todas"])
        parser.add_argument("--usuario", required=True)
        parser.add_argument("--periodo", help="Mes YYYY-MM; por defecto el actual")
        parser.add_argument("--automatico", action="store_true", help="Respeta ventana y hora configuradas")
        parser.add_argument("--probar", action="store_true", help="Solo valida lectura; no persiste eventos")
        parser.add_argument("--conciliar", action="store_true", help="Solo reintenta conciliaciones existentes")

    def handle(self, *args, **options):
        try:
            user = get_user_model().objects.get(username=options["usuario"])
        except get_user_model().DoesNotExist as exc:
            raise CommandError("Usuario no encontrado.") from exc
        if options["conciliar"]:
            self.stdout.write(str(conciliar_eventos(user)))
            return
        credentials = CredencialBanco.objects.filter(user=user, activo=True, conector__in=("houm", "neat"))
        if options["fuente"] != "todas":
            credentials = credentials.filter(conector=options["fuente"])
        if not credentials.exists():
            raise CommandError("No hay fuentes configuradas para ese usuario.")
        period = None
        if options["periodo"]:
            try:
                period = date.fromisoformat(options["periodo"] + "-01")
            except ValueError as exc:
                raise CommandError("El período debe usar YYYY-MM.") from exc
        for credential in credentials:
            if options["probar"]:
                count = probar_conexion(credential)
                self.stdout.write(self.style.SUCCESS(f"{credential.conector}: conexión válida ({count})"))
                continue
            log = run_external_sync(credential, manual=not options["automatico"], period=period)
            if log is None:
                self.stdout.write(self.style.WARNING(f"{credential.conector}: ya está ejecutándose"))
            elif log.exito:
                self.stdout.write(self.style.SUCCESS(f"{credential.conector}: {log.mensaje}"))
            else:
                self.stdout.write(self.style.ERROR(f"{credential.conector}: {log.mensaje}"))
