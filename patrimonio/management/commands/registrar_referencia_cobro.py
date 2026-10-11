"""Carga una referencia aportada sin confirmarla ni cambiar el saldo vigente."""

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from patrimonio.models import CuentaPorCobrar
from patrimonio.services.revision_cobros import registrar_referencia


class Command(BaseCommand):
    help = "Importa un cálculo histórico como referencia sin validar; simula por defecto."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", type=int, required=True)
        parser.add_argument("--cuenta-id", type=int, required=True)
        parser.add_argument("--archivo", required=True)
        parser.add_argument("--apply", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            cuenta = CuentaPorCobrar.objects.get(pk=options["cuenta_id"], user_id=options["user_id"])
            datos = json.loads(Path(options["archivo"]).read_text(encoding="utf-8"))
            detalles_ids, abonos_ids = datos["detalle_ids"], datos["abono_ids"]
            detalles = list(cuenta.detalles.filter(pk__in=detalles_ids))
            abonos = list(cuenta.abonos.filter(pk__in=abonos_ids))
            if len(detalles) != len(set(detalles_ids)) or len(abonos) != len(set(abonos_ids)):
                raise CommandError("La referencia contiene registros inexistentes o de otra cuenta.")
            calculo = registrar_referencia(
                cuenta, date.fromisoformat(datos["periodo"]), date.fromisoformat(datos["fecha"]),
                Decimal(datos.get("saldo_anterior", "0")), detalles=detalles, abonos=abonos,
                cargos=datos.get("cargos"), texto=datos.get("texto", ""),
                monto_esperado=datos["saldo_final"], usuario=True,
            )
        except (CuentaPorCobrar.DoesNotExist, KeyError, ValueError, InvalidOperation, OSError, ValidationError) as error:
            raise CommandError(str(error)) from error
        if not options["apply"]:
            transaction.set_rollback(True)
        self.stdout.write(f"{'Guardada' if options['apply'] else 'Simulación'}: referencia {calculo.fecha_calculo}, saldo {calculo.saldo_final} {cuenta.moneda}. No se confirma ni altera el saldo vigente.")
