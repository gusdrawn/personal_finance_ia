"""Prepara una reconstrucción histórica sin confirmar ni cambiar la valoración."""

from datetime import date
import json

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from patrimonio.models import CuentaPorCobrar
from patrimonio.services.revision_cobros import preparar_calculo


class Command(BaseCommand):
    help = "Reconstruye un mes con sus fuentes disponibles como borrador; simula por defecto."

    def add_arguments(self, parser):
        parser.add_argument("--user-id", required=True, type=int)
        parser.add_argument("--cuenta-id", required=True, type=int)
        parser.add_argument("--periodo", required=True, help="Mes YYYY-MM")
        parser.add_argument("--referencia-id", type=int)
        parser.add_argument("--apply", action="store_true", help="Guardar sólo el borrador y sus partidas pendientes")

    def handle(self, *args, **options):
        try:
            periodo = date.fromisoformat(options["periodo"] + "-01")
        except ValueError as error:
            raise CommandError("El período debe tener formato YYYY-MM.") from error
        cuenta = CuentaPorCobrar.objects.filter(pk=options["cuenta_id"], user_id=options["user_id"]).first()
        if cuenta is None:
            raise CommandError("La cuenta no pertenece al usuario indicado.")
        try:
            with transaction.atomic():
                calculo = preparar_calculo(cuenta, periodo, options["referencia_id"])
                resultado = dict(simulacion=not options["apply"], periodo=str(calculo.periodo),
                                 estado=calculo.estado, saldo_propuesto=str(calculo.saldo_final),
                                 partidas=calculo.desglose["entradas"],
                                 preparacion=calculo.desglose.get("preparacion", {}))
                if not options["apply"]:
                    transaction.set_rollback(True)
        except ValidationError as error:
            raise CommandError(" ".join(error.messages)) from error
        self.stdout.write(json.dumps(resultado, ensure_ascii=False, indent=2))
