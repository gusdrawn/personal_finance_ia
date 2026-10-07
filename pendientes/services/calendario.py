from __future__ import annotations

import calendar
from datetime import date, timedelta

from django.db import transaction

from pendientes.models import OcurrenciaPendiente, ReglaPendiente


MESES_POR_PERIODICIDAD = {
    "MENSUAL": 1,
    "BIMESTRAL": 2,
    "TRIMESTRAL": 3,
    "SEMESTRAL": 6,
    "ANUAL": 12,
}


def sumar_meses(fecha: date, meses: int) -> date:
    total = fecha.year * 12 + fecha.month - 1 + meses
    year, month = divmod(total, 12)
    return date(year, month + 1, min(fecha.day, calendar.monthrange(year, month + 1)[1]))


def _fecha_con_dia(year: int, month: int, dia: int | None) -> date:
    return date(year, month, min(dia or 1, calendar.monthrange(year, month)[1]))


def _fecha_desde_inicio(regla: ReglaPendiente, meses: int = 0) -> date:
    inicio = regla.fecha_inicio or date.today()
    dia = regla.dia_vencimiento or inicio.day
    fecha = sumar_meses(inicio.replace(day=1), meses)
    return _fecha_con_dia(fecha.year, fecha.month, dia)


def fechas_para_regla(regla: ReglaPendiente, hoy: date | None = None, horizonte_meses: int = 15) -> list[date]:
    """Return a small future window; occurrence uniqueness makes this idempotent."""
    hoy = hoy or date.today()
    if regla.periodicidad == "DESDE_ULTIMO_REGISTRO":
        ultima = regla.ocurrencias.order_by("-fecha_vencimiento").first()
        if ultima and ultima.estado == "PENDIENTE":
            return [ultima.fecha_vencimiento]
        base = ultima.fecha_vencimiento + timedelta(days=regla.umbral_dias_inactividad) if ultima else (regla.fecha_inicio or hoy)
        return [base] if base >= hoy - timedelta(days=regla.umbral_dias_inactividad) else [hoy]
    if regla.periodicidad == "UNICA":
        return [regla.fecha_inicio] if regla.fecha_inicio else []
    if regla.periodicidad == "MESES":
        meses = {int(m) for m in (regla.meses_vencimiento or []) if str(m).isdigit() and 1 <= int(m) <= 12}
        if not meses:
            return []
        result = []
        for offset in range(-1, horizonte_meses + 1):
            f = sumar_meses(hoy.replace(day=1), offset)
            if f.month in meses:
                result.append(_fecha_con_dia(f.year, f.month, regla.dia_vencimiento or 30))
        return result
    step = MESES_POR_PERIODICIDAD.get(regla.periodicidad)
    if not step:
        return []
    inicio = regla.fecha_inicio or hoy
    meses_transcurridos = (hoy.year - inicio.year) * 12 + hoy.month - inicio.month
    primer_k = max(0, (meses_transcurridos // step) * step - step)
    return [_fecha_desde_inicio(regla, primer_k + step * n) for n in range(0, (horizonte_meses // step) + 3)]


@transaction.atomic
def materializar_ocurrencias(regla: ReglaPendiente, hoy: date | None = None) -> list[OcurrenciaPendiente]:
    if not regla.activa:
        return []
    creadas = []
    for fecha in fechas_para_regla(regla, hoy=hoy):
        if not fecha:
            continue
        ocurrencia, created = OcurrenciaPendiente.objects.get_or_create(
            regla=regla,
            fecha_vencimiento=fecha,
            defaults={"monto_esperado": regla.monto_estimado},
        )
        if created:
            creadas.append(ocurrencia)
    return creadas


def materializar_usuario(user, hoy: date | None = None) -> int:
    total = 0
    for regla in ReglaPendiente.objects.filter(user=user, activa=True):
        total += len(materializar_ocurrencias(regla, hoy=hoy))
    return total
