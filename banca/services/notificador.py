"""
Notificador por Telegram (ver §14.4).

Requiere TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en el entorno. Avisa del presupuesto Ocio
en curso (devengo) y, opcionalmente, de los gastos nuevos del día.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal

logger = logging.getLogger("banca.notificador")


def _miles(n: Decimal | int) -> str:
    try:
        return f"${int(n):,}".replace(",", ".")
    except Exception:
        return f"${n}"


def enviar_telegram(texto: str, *, chat_id: str | None = None) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        logger.info("Telegram no configurado (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID).")
        return False
    try:
        import requests

        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": texto, "parse_mode": "HTML"},
            timeout=20,
        )
        resp.raise_for_status()
        return True
    except Exception as e:  # pragma: no cover
        logger.warning("Falló envío Telegram: %s", e)
        return False


def avisar_presupuesto(user, *, forzar: bool = False) -> bool:
    """Manda el estado del presupuesto Ocio si se cruzó el umbral (o si forzar=True)."""
    from banca.services.presupuesto import resumen_presupuesto

    r = resumen_presupuesto(user)
    if not r["alerta"] and not forzar:
        return False

    emoji = "🔴" if r["alerta"] else "🟢"
    texto = (
        f"{emoji} <b>Presupuesto Ocio {r['month']:02d}/{r['year']}</b>\n"
        f"Disponible: {_miles(r['disponible_ocio'])}\n"
        f"Gastado (Ocio): {_miles(r['ocio_gastado'])} ({r['porcentaje_usado']}%)\n"
        f"Restante: {_miles(r['ocio_restante'])}\n"
        f"<i>Ingreso prom. {r['ventana_meses']}m: {_miles(r['promedio_ingresos'])} · "
        f"Comprometido: {_miles(r['comprometido'])}</i>"
    )
    return enviar_telegram(texto)


def avisar_nuevos_no_facturados(user, ids: list[int]) -> bool:
    """Resumen breve de los gastos nuevos detectados (no facturados)."""
    from banca.models import MovimientoBancario

    movs = MovimientoBancario.objects.filter(
        id__in=ids, user=user, naturaleza="OPERACIONAL", monto_clp__gt=0
    ).order_by("-fecha")[:10]
    if not movs:
        return False
    lineas = [f"• {m.fecha:%d/%m} {m.descripcion[:30]} {_miles(m.monto_clp)}" for m in movs]
    texto = "🧾 <b>Nuevos gastos detectados</b>\n" + "\n".join(lineas)
    enviado = enviar_telegram(texto)
    if enviado:
        MovimientoBancario.objects.filter(id__in=[m.id for m in movs]).update(notificado_telegram=True)
    return enviado
