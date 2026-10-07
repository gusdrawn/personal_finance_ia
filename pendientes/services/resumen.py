from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db.models import Sum

from pendientes.models import AsignacionPagoPendiente, OcurrenciaPendiente, ReglaPendiente


def resumen_propiedad(departamento, hoy: date | None = None) -> dict:
    hoy = hoy or date.today()
    reglas = ReglaPendiente.objects.filter(departamento=departamento, activa=True, tipo="PAGO")
    asignaciones = AsignacionPagoPendiente.objects.filter(
        ocurrencia__regla__departamento=departamento,
        ocurrencia__estado="COMPLETADA",
    )
    real = asignaciones.aggregate(total=Sum("monto_asignado"))["total"] or Decimal("0")
    proyectado = OcurrenciaPendiente.objects.filter(
        regla__in=reglas,
        estado="PENDIENTE",
        fecha_vencimiento__gte=hoy,
    ).exclude(regla__canal_cobro="INCLUIDO_CONTRIBUCIONES").aggregate(total=Sum("monto_esperado"))["total"] or Decimal("0")
    return {
        "reglas": reglas,
        "real": real,
        "proyectado": proyectado,
        "aseo_configurado": reglas.filter(nombre__icontains="aseo").exists(),
        "contribuciones_configuradas": reglas.filter(nombre__icontains="contrib").exists(),
    }
