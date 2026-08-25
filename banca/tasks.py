"""
Tareas Celery para correr la sincronización en background / programada.

Para programar (1× al día no facturados, 1× al mes estado de cuenta) usa Celery Beat o
el scheduler que prefieras, apuntando a estas tareas.
"""

from __future__ import annotations

try:
    from celery import shared_task
except Exception:  # Celery opcional en dev

    def shared_task(*a, **k):
        def deco(fn):
            return fn

        return deco if not (a and callable(a[0])) else a[0]


@shared_task
def sync_todas(modo: str = "full"):
    """Sincroniza todas las credenciales activas."""
    from banca.models import CredencialBanco
    from banca.services.sync import run_sync

    resultados = []
    for cred in CredencialBanco.objects.filter(activo=True):
        log = run_sync(cred, modo=modo, headless=True)
        resultados.append(
            {"banco": str(cred.banco), "exito": log.exito, "nuevos": log.nuevos}
        )
    return resultados


@shared_task
def sync_no_facturados_diario():
    return sync_todas("no_facturado")


@shared_task
def sync_estado_cuenta_mensual():
    return sync_todas("estado_cuenta")


@shared_task
def sync_activos_diario():
    """
    Sincroniza SOLO las fuentes de activos (cripto/inversión/AFP) vía API.

    No abre navegador, así que es segura para correr programada (a diferencia de la sync de
    bancos con Playwright). Alimenta patrimonio.Activo / HistorialActivo.
    """
    from bank_sync.registry import es_fuente_activos
    from banca.models import CredencialBanco
    from banca.services.sync import run_sync

    resultados = []
    for cred in CredencialBanco.objects.filter(activo=True):
        try:
            if not es_fuente_activos(cred.conector):
                continue
        except Exception:
            continue
        log = run_sync(cred, modo="full")
        resultados.append(
            {"fuente": str(cred.banco), "exito": log.exito, "nuevos": log.nuevos}
        )
    return resultados
