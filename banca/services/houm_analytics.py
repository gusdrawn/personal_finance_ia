"""Métricas históricas de Houm para el portafolio de departamentos."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from banca.models import LiquidacionHoum


ZERO = Decimal("0")


def _metrics(liquidation):
    administration = liquidation.evento.comision
    other_costs = (
        liquidation.corretaje
        + liquidation.promociones
        + liquidation.reparaciones
        + liquidation.servicios
    )
    expected_net = (
        liquidation.renta_bruta - administration - other_costs + liquidation.ajustes
    )
    gap = expected_net - liquidation.deposito_neto
    return {
        "renta": liquidation.renta_bruta,
        "administracion": administration,
        "otros_costos": other_costs,
        "ajustes": liquidation.ajustes,
        "deposito": liquidation.deposito_neto,
        "deficit": max(gap, ZERO),
        "excedente": max(-gap, ZERO),
    }


def resumen_houm_departamentos(user, meses=12):
    """Resume liquidaciones y unidades sin ingreso del último reporte del usuario."""
    settlements = list(
        LiquidacionHoum.objects.filter(
            evento__user=user,
            evento__periodo__isnull=False,
            vinculo_propiedad__user=user,
            vinculo_propiedad__departamento__user=user,
            vinculo_propiedad__activo=True,
        )
        .exclude(evento__estado__in=("FALLIDO", "REVERSADO"))
        .select_related("evento", "vinculo_propiedad__departamento")
        .order_by("evento__periodo", "pk")
    )
    if not settlements:
        return {
            "periodo_actual": None,
            "actual": _empty_metrics(),
            "historial": [],
            "por_departamento": {},
            "sin_ingreso_count": 0,
        }

    by_period = defaultdict(_empty_metrics)
    by_department = defaultdict(list)
    for settlement in settlements:
        metrics = _metrics(settlement)
        period = settlement.evento.periodo
        _add_metrics(by_period[period], metrics)
        by_department[settlement.vinculo_propiedad.departamento_id].append(
            {"periodo": period, "estado": settlement.evento.estado, **metrics}
        )

    current_period = max(by_period)
    department_summary = {}
    for department_id, rows in by_department.items():
        current = next((row for row in reversed(rows) if row["periodo"] == current_period), None)
        if current:
            current["sin_ingreso"] = False
            current["renta_esperada"] = current["renta"]
        averages = _empty_metrics()
        for row in rows:
            _add_metrics(averages, row)
        count = Decimal(len(rows))
        for field in averages:
            averages[field] /= count
        department_summary[department_id] = {
            "actual": current,
            "promedio": averages,
            "meses": len(rows),
        }

    # Cuando Houm informa el mes pero omite una unidad activa, el ingreso del mes
    # es cero. Si existe una renta contractual activa, esa renta no recibida es el
    # déficit; no se arrastra el promedio histórico para ocultar la vacancia.
    from departamentos.models import Departamento

    missing_count = 0
    departments = Departamento.objects.filter(user=user, activo=True).select_related(
        "arrendatario"
    )
    for department in departments:
        summary = department_summary.get(department.id)
        if summary and summary["actual"]:
            continue
        tenant = getattr(department, "arrendatario", None)
        expected_rent = (
            tenant.monto_arriendo_clp if tenant and tenant.activo else ZERO
        )
        previous_rows = by_department.get(department.id, [])
        if not expected_rent and previous_rows:
            expected_rent = previous_rows[-1]["renta"]
        missing = {
            "periodo": current_period,
            "estado": "SIN_INGRESO",
            "sin_ingreso": True,
            "renta_esperada": expected_rent,
            **_empty_metrics(),
        }
        missing["deficit"] = expected_rent
        by_period[current_period]["deficit"] += expected_rent
        if summary:
            summary["actual"] = missing
        else:
            department_summary[department.id] = {
                "actual": missing,
                "promedio": _empty_metrics(),
                "meses": 0,
            }
        missing_count += 1

    history_periods = sorted(by_period)[-meses:]
    history = [
        {"periodo": period, "etiqueta": period.strftime("%m/%Y"), **by_period[period]}
        for period in history_periods
    ]

    return {
        "periodo_actual": current_period,
        "actual": by_period[current_period],
        "historial": history,
        "por_departamento": department_summary,
        "sin_ingreso_count": missing_count,
    }


def _empty_metrics():
    return {
        "renta": ZERO,
        "administracion": ZERO,
        "otros_costos": ZERO,
        "ajustes": ZERO,
        "deposito": ZERO,
        "deficit": ZERO,
        "excedente": ZERO,
    }


def _add_metrics(target, source):
    for field in target:
        target[field] += source[field]
