"""
Recordatorios de pago a partir del Calendario, notificados por Telegram.

Reúne los vencimientos próximos desde `gastos.GastoProgramado`. Los vencimientos de
TDC y fuentes externas se muestran en Calendario, sin recrear recordatorios legados.

y los envía por Telegram reutilizando `banca.services.notificador.enviar_telegram`.

Diseñado para correr programado (Celery Beat, ver core/tasks.py) usando las credenciales
y preferencias cifradas de cada usuario.
"""

from __future__ import annotations

import calendar
import hashlib
from datetime import date, timedelta
from decimal import Decimal

# meses que suma cada frecuencia de GastoProgramado
PERIODO_MESES = {
    "MENSUAL": 1,
    "BIMESTRAL": 2,
    "TRIMESTRAL": 3,
    "SEMESTRAL": 6,
    "ANUAL": 12,
}


def _sumar_meses(base: date, meses: int) -> date:
    """Suma `meses` a una fecha conservando el día (recortado al largo del mes destino)."""
    total = base.month - 1 + meses
    anio = base.year + total // 12
    mes = total % 12 + 1
    dia = min(base.day, calendar.monthrange(anio, mes)[1])
    return date(anio, mes, dia)


def proxima_ocurrencia_gasto(gasto, hoy: date) -> date | None:
    """Próxima fecha (>= hoy) en que corresponde pagar un GastoProgramado."""
    if gasto.fecha_inicio > hoy:
        return gasto.fecha_inicio
    step = PERIODO_MESES.get(gasto.frecuencia, 1)
    # k = meses transcurridos desde el inicio, redondeado hacia arriba al múltiplo de step
    meses = (hoy.year - gasto.fecha_inicio.year) * 12 + (
        hoy.month - gasto.fecha_inicio.month
    )
    k = (meses // step) * step
    f = _sumar_meses(gasto.fecha_inicio, k)
    while f < hoy:
        k += step
        f = _sumar_meses(gasto.fecha_inicio, k)
    return f


def proximo_dia_cobro(dia: int, hoy: date) -> date:
    """Próxima fecha (>= hoy) cuyo día del mes sea `dia` (recortado al largo del mes)."""
    dia_mes = min(dia, calendar.monthrange(hoy.year, hoy.month)[1])
    candidato = date(hoy.year, hoy.month, dia_mes)
    if candidato >= hoy:
        return candidato
    siguiente = _sumar_meses(date(hoy.year, hoy.month, 1), 1)
    dia_mes = min(dia, calendar.monthrange(siguiente.year, siguiente.month)[1])
    return date(siguiente.year, siguiente.month, dia_mes)


def vencimientos_proximos(
    user, *, dias: int = 3, hoy: date | None = None
) -> list[dict]:
    """Lista ordenada de vencimientos en [hoy, hoy+dias]. Cada item: fecha/nombre/monto/tipo."""
    from gastos.models import GastoProgramado

    hoy = hoy or date.today()
    limite = hoy + timedelta(days=dias)
    items: list[dict] = []

    for g in GastoProgramado.objects.filter(user=user, activo=True):
        f = proxima_ocurrencia_gasto(g, hoy)
        if f and hoy <= f <= limite:
            items.append(
                {"fecha": f, "nombre": g.nombre, "monto": g.monto, "tipo": "programado"}
            )

    items.sort(key=lambda x: (x["fecha"], x["nombre"]))
    return items


def _miles(n) -> str:
    try:
        return f"${int(n):,}".replace(",", ".")
    except Exception:
        return f"${n}"


def enviar_recordatorios(user, *, dias: int = 3, hoy: date | None = None) -> bool:
    """Envía por Telegram los vencimientos próximos del usuario. False si no hay ninguno."""
    from banca.services.notificador import enviar_telegram

    items = vencimientos_proximos(user, dias=dias, hoy=hoy)
    if not items:
        return False

    try:
        cfg = user.config_notificaciones
    except Exception:
        return False
    if not cfg.avisar_vencimientos:
        return False
    enviado = False
    for it in items:
        monto = f" · {_miles(it['monto'])}" if it["monto"] is not None else ""
        texto = (
            f"📅 <b>Pago próximo ({dias} días)</b>\n"
            f"• {it['fecha']:%d/%m} {it['nombre']}{monto}"
        )
        identidad = f"{it['tipo']}:{it['nombre']}:{it['fecha'].isoformat()}"
        clave = f"vencimiento:{hashlib.sha256(identidad.encode()).hexdigest()}"
        enviado = enviar_telegram(
            texto,
            user=user,
            tipo="VENCIMIENTO",
            clave_idempotencia=clave,
        ) or enviado
    return enviado
