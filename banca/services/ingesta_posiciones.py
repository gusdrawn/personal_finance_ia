"""
Ingesta de POSICIONES: objetos normalizados de fuentes de activos -> ORM de patrimonio.

A diferencia de `ingesta.py` (bancos -> MovimientoBancario/SaldoSnapshot), las fuentes de
activos (cripto, inversiones, AFP) reportan tenencias (`PosicionNormalizada`) que alimentan
`patrimonio.Activo` + `patrimonio.HistorialActivo`.

Idempotente: el Activo se identifica por usuario, fuente, cuenta y símbolo; el histórico
por (activo, fecha). Cada lote se guarda completo o se revierte si una posición falla.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from typing import Optional

from django.db import transaction
from django.utils import timezone

from bank_sync.models import PosicionNormalizada
from banca.services.ingesta import _valor_uf

# tipos válidos en patrimonio.Activo.TIPO_ACTIVO; si el connector manda otro, cae en OTRO
_TIPOS_ACTIVO_VALIDOS = {
    "EFECTIVO",
    "CRIPTO",
    "FONDO_MUTUO",
    "ACCIONES",
    "BROKERAGE",
    "PLAZO_FIJO",
    "PRESTAMO_DADO",
    "DEPARTAMENTO",
    "AHORRO",
    "OTRO",
}
_HORIZONTES_VALIDOS = {"EFECTIVO", "CORTO_PLAZO", "MEDIANO_PLAZO", "LARGO_PLAZO"}


def _valor_dolar(fecha: date | None = None) -> Optional[Decimal]:
    """Latest USD rate on or before the position valuation date."""
    try:
        from configuracion.models import TipoCambio

        fecha_valoracion = fecha or timezone.localdate()
        tc = (
            TipoCambio.objects.filter(fuente="mindicador.cl", fecha__lte=fecha_valoracion)
            .order_by("-fecha", "-id")
            .first()
            or TipoCambio.objects.filter(fecha__lte=fecha_valoracion)
            .order_by("-fecha", "-id")
            .first()
        )
        return tc.dolar if tc and tc.dolar else None
    except Exception:
        return None


def _valorizar(
    pos: PosicionNormalizada, dolar: Optional[Decimal]
) -> tuple[Decimal, Decimal]:
    """
    Devuelve (monto_clp, monto_usd) de una posición.

    Prioridad: valor_clp/valor_usd explícitos > cantidad*precio_unitario en su moneda.
    Rellena la moneda faltante con el tipo de cambio del USD si está disponible.
    """
    base_clp = pos.valor_clp
    base_usd = pos.valor_usd

    if (
        base_clp is None
        and base_usd is None
        and pos.cantidad is not None
        and pos.precio_unitario is not None
    ):
        total = pos.cantidad * pos.precio_unitario
        if pos.moneda == "USD":
            base_usd = total
        else:
            base_clp = total

    if base_clp is None and base_usd is not None and dolar:
        base_clp = base_usd * dolar
    if base_usd is None and base_clp is not None and dolar:
        base_usd = base_clp / dolar

    q = Decimal("0.01")
    clp = (base_clp or Decimal("0")).quantize(q, rounding=ROUND_HALF_UP)
    usd = (base_usd or Decimal("0")).quantize(q, rounding=ROUND_HALF_UP)
    return clp, usd


def _normalizar_tipo(tipo: str) -> str:
    t = (tipo or "").strip().upper()
    return t if t in _TIPOS_ACTIVO_VALIDOS else "OTRO"


def _resolver_instrumento(pos: PosicionNormalizada):
    from trading.models import Instrument, InstrumentExternalId

    external = InstrumentExternalId.objects.filter(
        provider=pos.fuente, external_id=pos.simbolo
    ).select_related("instrument").first()
    if external:
        return external.instrument
    matches = list(Instrument.objects.filter(symbol__iexact=pos.simbolo)[:2])
    return matches[0] if len(matches) == 1 else None


def _saldo_original(pos: PosicionNormalizada) -> tuple[Decimal, str]:
    if pos.moneda == "USD" and pos.valor_usd is not None:
        monto, moneda = pos.valor_usd, "USD"
    elif pos.moneda == "CLP" and pos.valor_clp is not None:
        monto, moneda = pos.valor_clp, "CLP"
    elif pos.cantidad is not None and pos.precio_unitario is not None:
        monto, moneda = pos.cantidad * pos.precio_unitario, pos.moneda
    elif pos.valor_usd is not None:
        monto, moneda = pos.valor_usd, "USD"
    elif pos.valor_clp is not None:
        monto, moneda = pos.valor_clp, "CLP"
    else:
        monto, moneda = Decimal("0"), pos.moneda
    # Quantities/prices retain source precision; only the persisted money is rounded.
    return monto.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), moneda


@transaction.atomic
def ingestar_posiciones(
    user,
    posiciones: list[PosicionNormalizada],
    *,
    fecha: Optional[date] = None,
    cuenta_fuente_id: str = "",
) -> dict:
    """
    Inserta/actualiza Activos y su snapshot histórico del día.

    Devuelve {'nuevos': n, 'actualizados': m, 'ids': [...]}.
    """
    from patrimonio.models import Activo, HistorialActivo

    fecha = fecha or timezone.localdate()
    dolar = _valor_dolar(fecha)
    try:
        from configuracion.models import TipoCambio

        tipo_cambio_fecha = TipoCambio.objects.filter(
            fuente="mindicador.cl", fecha__lte=fecha
        ).order_by("-fecha", "-id").first() or TipoCambio.objects.filter(
            fecha__lte=fecha
        ).order_by("-fecha", "-id").first()
        uf = tipo_cambio_fecha.uf if tipo_cambio_fecha else None
    except Exception:
        uf = _valor_uf()

    nuevos, actualizados, ids = 0, 0, []
    for pos in posiciones:
        tipo = _normalizar_tipo(pos.tipo_activo)
        nombre = pos.nombre or pos.simbolo
        monto_clp, monto_usd = _valorizar(pos, dolar)

        cuenta_fuente = cuenta_fuente_id or pos.cuenta_fuente_id
        simbolo_fuente = (pos.simbolo or nombre).strip().casefold()
        instrumento = _resolver_instrumento(pos)
        defaults = dict(
            tipo=tipo,
            monto_clp=monto_clp,
            monto_usd=monto_usd,
            es_liquido=pos.es_liquido,
            fuente_externa=pos.fuente[:40],
            cuenta_fuente=cuenta_fuente[:120],
            id_fuente=simbolo_fuente[:180],
            instrumento=instrumento,
            requiere_revision_fuente=instrumento is None,
        )
        saldo_original, moneda_original = _saldo_original(pos)
        activo = Activo.objects.filter(
            user=user,
            fuente_externa=defaults["fuente_externa"],
            cuenta_fuente=defaults["cuenta_fuente"],
            id_fuente=defaults["id_fuente"],
        ).first()

        # Upgrade a single legacy row created by the old (user, type, name) key.
        # Its source remains flagged because the previous schema did not retain
        # enough information to prove that the name was a unique source identity.
        if activo is None:
            legacy = list(
                Activo.objects.filter(
                    user=user, tipo=tipo, nombre=nombre, fuente_externa="", id_fuente=""
                )[:2]
            )
            if len(legacy) == 1:
                activo = legacy[0]
                activo.fuente_externa = defaults["fuente_externa"]
                activo.cuenta_fuente = defaults["cuenta_fuente"]
                activo.id_fuente = defaults["id_fuente"]
                activo.requiere_revision_fuente = True
                for field, value in defaults.items():
                    setattr(activo, field, value)
                activo.requiere_revision_fuente = True
                activo.asignar_saldo_original(saldo_original, moneda_original)
                activo.save()
                actualizados += 1
            else:
                activo = Activo.objects.create(
                    user=user,
                    nombre=nombre,
                    monto_original=saldo_original,
                    moneda_original=moneda_original,
                    **defaults,
                )
                nuevos += 1
        else:
            for field, value in defaults.items():
                setattr(activo, field, value)
            activo.asignar_saldo_original(saldo_original, moneda_original)
            activo.save()
            actualizados += 1
        if pos.horizonte in _HORIZONTES_VALIDOS:
            activo.horizonte_temporal = pos.horizonte
            activo.save(update_fields=["horizonte_temporal", "updated_at"])
            defaults["horizonte_temporal"] = pos.horizonte
        ids.append(activo.id)

        # snapshot histórico del día (idempotente por unique (activo, fecha))
        HistorialActivo.objects.update_or_create(
            activo=activo,
            fecha=fecha,
            defaults=dict(
                monto_clp=monto_clp,
                monto_usd=monto_usd,
                valor_uf=uf,
                valor_dolar=dolar,
            ),
        )

    return {"nuevos": nuevos, "actualizados": actualizados, "ids": ids}
