"""
Comando de sincronización bancaria.

Ejemplos:
    python manage.py sync_banco --banco santander --modo no_facturado
    python manage.py sync_banco --banco santander --modo estado_cuenta --no-headless
    python manage.py sync_banco --modo full            # todas las credenciales activas
"""

from django.core.management.base import BaseCommand

from banca.models import CredencialBanco
from banca.services.sync import run_sync


class Command(BaseCommand):
    help = "Sincroniza movimientos, estados de cuenta y deudas desde el banco."

    def add_arguments(self, parser):
        parser.add_argument("--banco", type=str, default=None, help="Nombre del connector (ej. santander)")
        parser.add_argument("--user", type=str, default=None, help="username (opcional, filtra credenciales)")
        parser.add_argument(
            "--modo", type=str, default="full",
            choices=["no_facturado", "estado_cuenta", "deudas", "full"],
        )
        parser.add_argument("--no-headless", action="store_true", help="Mostrar el navegador (debug)")
        parser.add_argument("--no-notify", action="store_true", help="No enviar Telegram")

    def handle(self, *args, **opts):
        qs = CredencialBanco.objects.filter(activo=True)
        if opts["banco"]:
            qs = qs.filter(conector=opts["banco"])
        if opts["user"]:
            qs = qs.filter(user__username=opts["user"])

        if not qs.exists():
            self.stderr.write(self.style.WARNING("No hay credenciales activas que coincidan."))
            return

        for cred in qs:
            self.stdout.write(f"→ Sincronizando {cred.banco} ({cred.rut}) modo={opts['modo']} ...")
            log = run_sync(
                cred,
                modo=opts["modo"],
                headless=not opts["no_headless"],
                notificar=not opts["no_notify"],
            )
            if log.exito:
                self.stdout.write(self.style.SUCCESS(
                    f"  OK · nuevos={log.nuevos} actualizados={log.actualizados} clasificados={log.clasificados_ia}"
                ))
            else:
                self.stderr.write(self.style.ERROR(f"  ERROR · {log.mensaje}"))
