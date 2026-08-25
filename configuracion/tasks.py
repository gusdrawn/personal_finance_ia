"""Tareas Celery de configuración: actualización automática de indicadores (UF/USD)."""

from __future__ import annotations

try:
    from celery import shared_task
except Exception:  # Celery opcional en dev

    def shared_task(*a, **k):
        def deco(fn):
            return fn

        return deco if not (a and callable(a[0])) else a[0]


@shared_task
def actualizar_tasas_diario():
    """Descarga UF y USD del día (mindicador.cl) y las guarda en TipoCambio."""
    from configuracion.utils import fetch_mindicador_rates

    tc, ok = fetch_mindicador_rates()
    if ok:
        return {
            "ok": True,
            "fecha": str(tc.fecha),
            "uf": str(tc.uf),
            "dolar": str(tc.dolar),
        }
    return {"ok": False}
