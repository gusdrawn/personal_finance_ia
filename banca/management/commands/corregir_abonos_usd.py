"""Repara la conversión CLP guardada históricamente como importe de un abono USD."""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F, Q

from patrimonio.models import AbonoCuentaPorCobrar


class Command(BaseCommand):
    help = "Detecta abonos USD guardados con el importe CLP; simula por defecto y corrige con --apply."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Aplica las correcciones verificables.")
        parser.add_argument("--user-id", type=int, help="Limita la revisión a un usuario.")

    @transaction.atomic
    def handle(self, *args, **options):
        candidatos = AbonoCuentaPorCobrar.objects.select_related("cuenta", "movimiento").filter(
            cuenta__moneda="USD", movimiento__moneda="USD",
            movimiento__monto__lt=0, movimiento__monto_clp__lt=0,
            movimiento__user_id=F("cuenta__user_id"),
        ).filter(Q(detalle__isnull=True) | Q(detalle__cuenta_id=F("cuenta_id")))
        if options["user_id"] is not None:
            candidatos = candidatos.filter(cuenta__user_id=options["user_id"])
        if options["apply"]:
            candidatos = candidatos.select_for_update()
        encontrados = 0
        for abono in candidatos.order_by("pk").iterator():
            original = abs(abono.movimiento.monto)
            convertido = abs(abono.movimiento.monto_clp)
            # No altera abonos parciales ni importes que ya están en USD.
            if abono.monto != convertido or original >= convertido:
                continue
            encontrados += 1
            if options["apply"]:
                abono.monto = original
                abono.save(update_fields=["monto", "updated_at"])
        accion = "Corregidos" if options["apply"] else "Detectados (simulación)"
        self.stdout.write(f"{accion}: {encontrados} abonos USD.")
