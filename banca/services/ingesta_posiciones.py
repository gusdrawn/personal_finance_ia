"""
Ingesta de POSICIONES: objetos normalizados de fuentes de activos -> ORM de patrimonio.

A diferencia de `ingesta.py` (bancos -> MovimientoBancario/SaldoSnapshot), las fuentes de
activos (cripto, inversiones, AFP) reportan tenencias (`PosicionNormalizada`) que alimentan
`patrimonio.Activo` + `patrimonio.HistorialActivo`.

Idempotente: el upsert del Activo es por (user, tipo, nombre); el histórico por (activo,
fecha), respetando el unique_together que ya existe en HistorialActivo.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from typing import Optional

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


def _valor_dolar() -> Optional[Decimal]:
    """Último valor del USD en CLP desde configuracion.TipoCambio."""
    try:
        from configuracion.models import TipoCambio

        tc = TipoCambio.objects.order_by("-fecha").first()
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


def ingestar_posiciones(
    user,
    posiciones: list[PosicionNormalizada],
    *,
    fecha: Optional[date] = None,
) -> dict:
    """
    Inserta/actualiza Activos y su snapshot histórico del día.

    Devuelve {'nuevos': n, 'actualizados': m, 'ids': [...]}.
    """
    from patrimonio.models import Activo, HistorialActivo

    fecha = fecha or timezone.localdate()
    dolar = _valor_dolar()
    uf = _valor_uf()

    nuevos, actualizados, ids = 0, 0, []
    for pos in posiciones:
        tipo = _normalizar_tipo(pos.tipo_activo)
        nombre = pos.nombre or pos.simbolo
        monto_clp, monto_usd = _valorizar(pos, dolar)

        defaults = dict(
            tipo=tipo,
            monto_clp=monto_clp,
            monto_usd=monto_usd,
            es_liquido=pos.es_liquido,
        )
        if pos.horizonte in _HORIZONTES_VALIDOS:
            defaults["horizonte_temporal"] = pos.horizonte

        activo, creado = Activo.objects.get_or_create(
            user=user, tipo=tipo, nombre=nombre, defaults=defaults
        )
        if creado:
            nuevos += 1
        else:
            activo.monto_clp = monto_clp
            activo.monto_usd = monto_usd
            campos = ["monto_clp", "monto_usd", "updated_at"]
            if pos.horizonte in _HORIZONTES_VALIDOS:
                activo.horizonte_temporal = pos.horizonte
                campos.append("horizonte_temporal")
            activo.save(update_fields=campos)
            actualizados += 1
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
