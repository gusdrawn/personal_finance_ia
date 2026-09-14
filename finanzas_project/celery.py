"""
App Celery del proyecto.

Habilita tareas en background y programadas (Celery Beat). La configuración se lee de
`settings.py` con el prefijo `CELERY_` (p.ej. CELERY_BROKER_URL -> broker_url).

Levantar:
    celery -A finanzas_project worker -B --pool=solo -l info
o usa el script `scripts/celery.sh`.
"""

from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "finanzas_project.settings")

app = Celery("finanzas")
# lee CELERY_* desde Django settings (broker_url, result_backend, timezone, etc.)
app.config_from_object("django.conf:settings", namespace="CELERY")
app.conf.timezone = "America/Santiago"
# descubre tasks.py en cada app instalada (banca, configuracion, ...)
app.autodiscover_tasks()

# --- Programación (Beat) -------------------------------------------------------------
# Solo se programa lo que NO abre navegador (cripto/inversiones vía API). La sync de
# bancos (Santander/Playwright) se deja manual o se agrega aquí a conciencia, porque puede
# gatillar validación de dispositivo.
app.conf.beat_schedule = {
    # Indicadores UF/USD del día (mindicador.cl) — temprano, antes de valorizar.
    "indicadores-diario": {
        "task": "configuracion.tasks.actualizar_tasas_diario",
        "schedule": crontab(hour=7, minute=0),
    },
    # Refresca posiciones de fuentes de activos (cripto/inversión/AFP) 1× al día.
    "sync-activos-diario": {
        "task": "banca.tasks.sync_activos_diario",
        "schedule": crontab(hour=8, minute=0),
    },
    # Evalúa configuraciones Houm/Neat. Cada fuente decide si hoy corresponde correr.
    "sync-fuentes-externas-configurables": {
        "task": "banca.tasks.sync_fuentes_externas_programadas",
        "schedule": crontab(hour="8-18", minute=0),
    },
    # Recordatorios de pago del Calendario por Telegram (vencen en 3 días).
    "recordatorios-pagos-diario": {
        "task": "core.tasks.recordatorios_pagos_diario",
        "schedule": crontab(hour=8, minute=30),
    },
    "alertas-financieras-periodicas": {
        "task": "core.tasks.alertas_financieras_periodicas",
        "schedule": crontab(minute="*/15"),
    },
    # Datos de mercado del scanner de altcoins (sin operaciones de trading).
    "sync-altcoins-mercado": {
        "task": "altcoins.tasks.sync_altcoin_market_task",
        "schedule": crontab(hour="*/6", minute=15),
    },
    "sync-altcoins-metadata": {
        "task": "altcoins.tasks.sync_altcoin_metadata_task",
        "schedule": crontab(day_of_week=0, hour=4, minute=30),
    },
    "sync-trading-diario": {
        "task": "trading.tasks.sync_trading_daily_task",
        "schedule": crontab(hour=18, minute=15),
    },
}


@app.task(bind=True)
def debug_task(self):  # pragma: no cover - utilitario
    print(f"Request: {self.request!r}")
