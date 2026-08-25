"""
Presupuesto por ingreso promediado (ver §15.4).

    Disponible_Ocio ≈ promedio_ingresos(ventana) − comprometido(FIJO + cuotas del mes)

El gasto Ocio se mide en DEVENGO (fecha de compra). Solo cuenta lo OPERACIONAL.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Q
from django.utils import timezone

from banca.models import ConfiguracionBanca, MovimientoBancario


def _operacionales(user):
    return MovimientoBancario.objects.filter(user=user, naturaleza="OPERACIONAL", por_revisar=False)


def promedio_ingresos(user, ventana_meses: int = 3) -> Decimal:
    """Promedio mensual de ingresos (abonos operacionales) en la ventana reciente."""
    hoy = timezone.localdate()
    # primer día de hace (ventana-1) meses
    y, m = hoy.year, hoy.month
    total_meses = m - (ventana_meses - 1)
    while total_meses <= 0:
        total_meses += 12
        y -= 1
    desde = hoy.replace(year=y, month=total_meses, day=1)

    abonos = _operacionales(user).filter(fecha__gte=desde, monto_clp__lt=0)
    por_mes = defaultdict(Decimal)
    for mov in abonos.only("fecha", "monto_clp"):
        por_mes[(mov.fecha.year, mov.fecha.month)] += -mov.monto_clp
    if not por_mes:
        return Decimal("0")
    total = sum(por_mes.values(), Decimal("0"))
    return (total / Decimal(len(por_mes))).quantize(Decimal("1"))


def comprometido_mes(user, year: int, month: int) -> Decimal:
    """FIJO + cuotas del mes (gasto comprometido que reduce el disponible)."""
    movs = _operacionales(user).filter(fecha__year=year, fecha__month=month, monto_clp__gt=0)
    comprometido = movs.filter(Q(esencialidad="FIJO") | Q(cuota_total__gt=1))
    total = sum((m.monto_clp for m in comprometido.only("monto_clp")), Decimal("0"))
    return total


def ocio_gastado_mes(user, year: int, month: int) -> Decimal:
    """Gasto Ocio del mes (devengo), excluyendo cuotas (que ya van en comprometido)."""
    movs = _operacionales(user).filter(
        fecha__year=year, fecha__month=month, monto_clp__gt=0, esencialidad="OCIO"
    ).filter(Q(cuota_total__isnull=True) | Q(cuota_total__lte=1))
    return sum((m.monto_clp for m in movs.only("monto_clp")), Decimal("0"))


def resumen_presupuesto(user, year: int | None = None, month: int | None = None) -> dict:
    hoy = timezone.localdate()
    year = year or hoy.year
    month = month or hoy.month
    cfg = ConfiguracionBanca.objects.filter(user=user).first()
    ventana = cfg.ventana_meses_ingreso if cfg else 3
    umbral = cfg.umbral_alerta_ocio if cfg else Decimal("0.80")

    ingreso_prom = promedio_ingresos(user, ventana)
    comprometido = comprometido_mes(user, year, month)
    ocio = ocio_gastado_mes(user, year, month)
    disponible = ingreso_prom - comprometido
    pct = (ocio / disponible) if disponible > 0 else Decimal("0")

    return {
        "year": year,
        "month": month,
        "ventana_meses": ventana,
        "promedio_ingresos": ingreso_prom,
        "comprometido": comprometido,
        "disponible_ocio": disponible,
        "ocio_gastado": ocio,
        "ocio_restante": disponible - ocio,
        "porcentaje_usado": (pct * 100).quantize(Decimal("0.1")),
        "alerta": bool(disponible > 0 and pct >= umbral),
        "umbral": (umbral * 100),
    }
