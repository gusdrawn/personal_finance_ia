"""
Orquestador de sincronización: conecta bank_sync con los servicios de banca.

Flujo (ver §14.4):
  - modo 'no_facturado': baja no facturados -> ingesta -> reconciliación -> clasificación
                         -> aviso de presupuesto por Telegram.
  - modo 'estado_cuenta': baja estados de cuenta -> ingesta (facturado) -> reconciliación
                         -> clasificación (quedan 'por confirmar' en el cierre de mes).
  - modo 'deudas': baja saldos -> snapshots -> (señal) actualiza pasivos del patrimonio.
  - modo 'full': las tres.

Importante: el API síncrono de Playwright corre un event loop en el hilo, y Django bloquea
el ORM en ese contexto "async". Por eso separamos en dos fases:
  1. EXTRACCIÓN (navegador abierto): solo se obtienen datos del banco, sin tocar el ORM.
  2. PERSISTENCIA (tras cerrar el navegador): ingesta, reconciliación, clasificación, avisos.
Además fijamos DJANGO_ALLOW_ASYNC_UNSAFE como red de seguridad (el job es de un solo hilo).
"""

from __future__ import annotations

import logging
import os

# El job corre en un solo hilo; permitimos ORM aunque Playwright deje un event loop activo.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from django.utils import timezone

from bank_sync.registry import es_fuente_activos, get_connector
from bank_sync.session import default_storage_path
from banca.models import CredencialBanco, SyncLog
from banca.services import (
    clasificacion_ia,
    ingesta,
    ingesta_posiciones,
    notificador,
    reconciliacion,
)

logger = logging.getLogger("banca.sync")


def run_sync(
    credencial: CredencialBanco,
    modo: str = "full",
    *,
    headless: bool = True,
    notificar: bool = True,
) -> SyncLog:
    """
    Sincroniza una fuente. Enruta según su tipo:
      - bancos  -> extracción de deudas/movimientos con navegador -> ORM de banca.
      - activos (cripto/inversión/AFP) -> posiciones vía API -> ORM de patrimonio.
    """
    if es_fuente_activos(credencial.conector):
        return _run_sync_activos(credencial, modo)
    return _run_sync_banco(credencial, modo, headless=headless, notificar=notificar)


def _run_sync_activos(credencial: CredencialBanco, modo: str = "full") -> SyncLog:
    """Fuente de activos: autentica (API key / dirección pública) -> posiciones -> patrimonio."""
    user = credencial.user
    banco = credencial.banco
    log = SyncLog.objects.create(user=user, banco=banco, modo=modo)

    # FASE 1: extracción (sin ORM). Los AssetConnector no usan navegador ni storage_state.
    connector = get_connector(credencial.conector)
    posiciones = None
    try:
        connector.authenticate(
            api_key=credencial.get_api_key(),
            api_secret=credencial.get_api_secret(),
            identificador_publico=credencial.identificador_publico or "",
        )
        posiciones = connector.get_posiciones()
    except Exception as e:
        logger.exception("Extracción de posiciones falló")
        log.exito = False
        log.mensaje = f"{type(e).__name__}: {e}"
        log.finalizado = timezone.now()
        log.save()
        return log
    finally:
        try:
            connector.close()
        except Exception:
            pass

    # FASE 2: persistencia
    try:
        res = ingesta_posiciones.ingestar_posiciones(user, posiciones or [])
        log.exito = True
        log.nuevos = res["nuevos"]
        log.actualizados = res["actualizados"]
        log.mensaje = "OK"
    except Exception as e:
        logger.exception("Persistencia de posiciones falló")
        log.exito = False
        log.mensaje = f"{type(e).__name__}: {e}"
    finally:
        log.finalizado = timezone.now()
        log.save()
    return log


def _run_sync_banco(
    credencial: CredencialBanco,
    modo: str = "full",
    *,
    headless: bool = True,
    notificar: bool = True,
) -> SyncLog:
    user = credencial.user
    banco = credencial.banco
    log = SyncLog.objects.create(user=user, banco=banco, modo=modo)
    nuevos = actualizados = clasificados = 0
    ids_nuevos: list[int] = []

    storage = default_storage_path(credencial.conector, str(user.id))
    connector = get_connector(
        credencial.conector, headless=headless, storage_state_path=storage
    )

    # --- FASE 1: extracción (navegador abierto, SIN ORM) ---
    deuda = movs = estados = None
    try:
        connector.login(credencial.rut, credencial.get_clave())
        if modo in ("deudas", "full"):
            deuda = connector.get_deudas()
        if modo in ("no_facturado", "full"):
            movs = connector.get_movimientos_no_facturados()
        if modo in ("estado_cuenta", "full"):
            estados = connector.get_estado_cuenta()
    except Exception as e:
        logger.exception("Extracción falló")
        log.exito = False
        log.mensaje = f"{type(e).__name__}: {e}"
        log.finalizado = timezone.now()
        log.save()
        return log
    finally:
        # cerrar el navegador antes de tocar el ORM (libera el event loop de Playwright)
        connector.logout()

    # --- FASE 2: persistencia (navegador cerrado, ORM libre) ---
    try:
        if deuda is not None:
            ingesta.ingestar_saldos(user, banco, deuda)
        if movs is not None:
            res = ingesta.ingestar_movimientos(user, banco, movs)
            nuevos += res["nuevos"]
            actualizados += res["actualizados"]
            ids_nuevos += res["ids"]
        if estados is not None:
            res = ingesta.ingestar_estado_cuenta(user, banco, estados)
            nuevos += res["nuevos"]
            actualizados += res["actualizados"]
            ids_nuevos += res["ids"]

        # post-proceso común
        reconciliacion.reconciliar(user, ids_nuevos or None)
        cls = clasificacion_ia.clasificar_pendientes(user, ids_nuevos or None)
        clasificados = cls.get("ia", 0) + cls.get("regla", 0)

        if notificar:
            if modo in ("no_facturado", "full"):
                notificador.avisar_nuevos_no_facturados(user, ids_nuevos)
            notificador.avisar_presupuesto(user)

        log.exito = True
        log.nuevos = nuevos
        log.actualizados = actualizados
        log.clasificados_ia = clasificados
        log.mensaje = "OK"
    except Exception as e:
        logger.exception("Persistencia falló")
        log.exito = False
        log.mensaje = f"{type(e).__name__}: {e}"
    finally:
        log.finalizado = timezone.now()
        log.save()
    return log
