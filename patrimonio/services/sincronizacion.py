"""Resolve asset integrations without writing data or guessing account ownership."""

import re
from dataclasses import dataclass

from django.utils import timezone

from banca.models import CredencialBanco, SyncLog
from bank_sync.exceptions import ConnectorNotFound
from bank_sync.registry import es_fuente_activos
from patrimonio.models import Activo, HistorialActivo


@dataclass(frozen=True)
class OrigenActivo:
    externo: bool = False
    credencial: object = None
    etiqueta: str = "Manual"


def origenes_activos(user, activos):
    """Use source/account identity, or corroborated legacy Binance sync evidence."""
    activos = list(activos)
    if not activos:
        return {}
    credenciales = []
    for credencial in CredencialBanco.objects.filter(user=user).select_related("banco"):
        try:
            if es_fuente_activos(credencial.conector):
                credenciales.append(credencial)
        except ConnectorNotFound:
            continue
    por_id = {str(c.pk): c for c in credenciales}
    legado = [
        a for a in activos
        if not a.fuente_externa and not a.cuenta_fuente and not a.id_fuente
        and a.tipo == "CRIPTO"
        and re.fullmatch(r"[A-Z0-9]+(?: Earn)? \(Binance\)", a.nombre)
        and a.updated_at
    ]
    historicos = set(HistorialActivo.objects.filter(
        activo__in=legado, activo__user=user,
    ).values_list("activo_id", "fecha")) if legado else set()
    binance = [c for c in credenciales if c.conector == "binance"]
    logs = list(SyncLog.objects.filter(
        user=user, banco_id__in=[c.banco_id for c in binance], exito=True,
        finalizado__isnull=False,
        iniciado__lte=max(a.updated_at for a in legado),
        finalizado__gte=min(a.updated_at for a in legado),
    )) if legado and binance else []
    nombres_repetidos = {}
    if legado:
        for nombre in Activo.objects.filter(
            user=user, tipo="CRIPTO", fuente_externa="", cuenta_fuente="", id_fuente="",
            nombre__in=[a.nombre for a in legado],
        ).values_list("nombre", flat=True):
            nombres_repetidos[nombre] = nombres_repetidos.get(nombre, 0) + 1
    origenes = {}
    for activo in activos:
        if activo.user_id != user.pk:
            continue
        if activo.fuente_externa or activo.cuenta_fuente or activo.id_fuente:
            c = por_id.get(activo.cuenta_fuente)
            if not (c and c.conector == activo.fuente_externa and activo.id_fuente):
                c = None
            origenes[activo.pk] = OrigenActivo(
                True, c if c and c.activo else None,
                c.banco.nombre if c else (activo.fuente_externa or "Fuente por revisar"),
            )
            continue
        candidatos = []
        if activo in legado:
            fecha = timezone.localdate(activo.updated_at)
            if (activo.pk, fecha) in historicos:
                candidatos = [
                    c for c in binance if any(
                        log.banco_id == c.banco_id
                        and log.iniciado <= activo.updated_at <= log.finalizado
                        and log.nuevos + log.actualizados > 0
                        for log in logs
                    )
                ]
        if len(candidatos) == 1 and nombres_repetidos.get(activo.nombre) == 1:
            c = candidatos[0]
            origenes[activo.pk] = OrigenActivo(True, c if c.activo else None, c.banco.nombre)
        elif candidatos:
            origenes[activo.pk] = OrigenActivo(True, None, "Binance · origen por confirmar")
        else:
            origenes[activo.pk] = OrigenActivo()
    return origenes


def origen_activo(activo):
    return origenes_activos(activo.user, [activo])[activo.pk]
