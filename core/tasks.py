"""Tareas Celery del core: recordatorios de pago del Calendario por Telegram."""

from __future__ import annotations

try:
    from celery import shared_task
except Exception:  # Celery opcional en dev

    def shared_task(*a, **k):
        def deco(fn):
            return fn

        return deco if not (a and callable(a[0])) else a[0]


@shared_task
def recordatorios_pagos_diario(dias: int = 3):
    """Avisa por Telegram los vencimientos próximos (próximos `dias` días) de cada usuario."""
    from django.contrib.auth.models import User

    from core.services.recordatorios import enviar_recordatorios

    enviados = 0
    for user in User.objects.filter(is_active=True):
        try:
            dias_usuario = user.config_notificaciones.dias_anticipacion_vencimientos
        except Exception:
            continue
        if enviar_recordatorios(user, dias=dias_usuario):
            enviados += 1
    return {"usuarios_notificados": enviados}


@shared_task
def alertas_financieras_periodicas():
    """Resumen diario y provisionales atrasados, según la configuración personal."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from django.contrib.auth.models import User

    from banca.services.notificador import (
        avisar_provisionales_atrasados,
        enviar_resumen_diario,
    )

    procesados = 0
    for user in User.objects.filter(is_active=True):
        try:
            cfg = user.config_notificaciones
            ahora = datetime.now(ZoneInfo(cfg.zona_horaria))
        except Exception:
            continue
        avisar_provisionales_atrasados(user, hoy=ahora.date())
        if cfg.resumen_diario:
            objetivo = cfg.hora_resumen_diario
            if ahora.hour == objetivo.hour and abs(ahora.minute - objetivo.minute) < 15:
                enviar_resumen_diario(user, hoy=ahora.date())
        procesados += 1
    return {"usuarios_procesados": procesados}
