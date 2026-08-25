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
        if enviar_recordatorios(user, dias=dias):
            enviados += 1
    return {"usuarios_notificados": enviados}
