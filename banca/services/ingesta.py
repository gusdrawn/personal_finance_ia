"""
Ingesta idempotente: objetos normalizados de bank_sync -> ORM de banca.

Clave anti-duplicación: el upsert es por `hash_externo` (huella estable entre NO_FACTURADO
y FACTURADO). Si el movimiento ya existe, se ACTUALIZA su estado/período; no se inserta de
nuevo. La clasificación manual del usuario nunca se pisa.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from django.utils import timezone

from bank_sync.models import DeudaResumen, EstadoCuenta, MovimientoNormalizado
from banca.models import MovimientoBancario, SaldoSnapshot


def _resolver_producto(banco, tarjeta_id: Optional[str], tipo: str = "TDC", nombre_hint: str = ""):
    """Mapea un identificador de tarjeta/crédito a un configuracion.Producto (get_or_create)."""
    from configuracion.models import Producto

    tid = (tarjeta_id or "").strip()
    if tid:
        prod = (
            Producto.objects.filter(banco=banco, tipo=tipo, nombre__icontains=tid).first()
            or Producto.objects.filter(banco=banco, nombre__icontains=tid).first()
        )
        if prod:
            return prod
        nombre = nombre_hint or f"{'TDC' if tipo == 'TDC' else tipo} *{tid}"
        prod, _ = Producto.objects.get_or_create(
            banco=banco, nombre=nombre, defaults={"tipo": tipo, "tiene_cupo_usd": False}
        )
        return prod
    # sin identificador: producto genérico por tipo
    prod, _ = Producto.objects.get_or_create(
        banco=banco, nombre=nombre_hint or f"{tipo} (sin asignar)", defaults={"tipo": tipo}
    )
    return prod


def _a_clp(mov: MovimientoNormalizado, tipo_cambio_usd: Optional[Decimal]) -> tuple[Decimal, Optional[Decimal]]:
    if mov.moneda == "USD":
        tc = tipo_cambio_usd
        if tc:
            return (mov.monto * tc, tc)
        return (Decimal("0"), None)  # se completará cuando haya TC del banco
    return (mov.monto, None)


def ingestar_movimientos(
    user,
    banco,
    movimientos: list[MovimientoNormalizado],
    *,
    tipo_cambio_usd: Optional[Decimal] = None,
) -> dict:
    """Inserta o actualiza movimientos. Devuelve {'nuevos': n, 'actualizados': m, 'ids': [...]}."""
    nuevos, actualizados, ids = 0, 0, []
    for mov in movimientos:
        h = mov.hash_externo()
        producto = _resolver_producto(banco, mov.tarjeta_id, "TDC")
        monto_clp, tc = _a_clp(mov, tipo_cambio_usd)

        existente = MovimientoBancario.objects.filter(user=user, hash_externo=h).first()
        if existente:
            # solo avanzar el ciclo de vida / período; respetar lo manual
            cambios = []
            if mov.estado == "FACTURADO" and existente.estado == "NO_FACTURADO":
                existente.estado = "FACTURADO"
                cambios.append("estado")
            if mov.periodo_factura and existente.periodo_factura != mov.periodo_factura:
                existente.periodo_factura = mov.periodo_factura
                cambios.append("periodo_factura")
            if tc and not existente.tipo_cambio_banco:
                existente.tipo_cambio_banco = tc
                existente.monto_clp = monto_clp
                cambios += ["tipo_cambio_banco", "monto_clp"]
            if cambios:
                existente.save(update_fields=cambios + ["updated_at"])
                actualizados += 1
            ids.append(existente.id)
            continue

        nuevo = MovimientoBancario.objects.create(
            user=user,
            producto=producto,
            fecha=mov.fecha,
            descripcion=mov.descripcion,
            monto=mov.monto,
            moneda=mov.moneda,
            monto_clp=monto_clp,
            tipo_cambio_banco=tc,
            cuota_actual=mov.cuota_actual,
            cuota_total=mov.cuota_total,
            estado=mov.estado,
            periodo_factura=mov.periodo_factura,
            hash_externo=h,
        )
        nuevos += 1
        ids.append(nuevo.id)
    return {"nuevos": nuevos, "actualizados": actualizados, "ids": ids}


def ingestar_estado_cuenta(user, banco, estados: list[EstadoCuenta]) -> dict:
    """Ingesta movimientos facturados de uno o más estados de cuenta."""
    total = {"nuevos": 0, "actualizados": 0, "ids": []}
    for ec in estados:
        res = ingestar_movimientos(
            user, banco, ec.movimientos, tipo_cambio_usd=ec.tipo_cambio_facturacion
        )
        total["nuevos"] += res["nuevos"]
        total["actualizados"] += res["actualizados"]
        total["ids"] += res["ids"]
    return total


def ingestar_saldos(user, banco, deuda: DeudaResumen, fecha: Optional[date] = None) -> int:
    """Crea/actualiza SaldoSnapshot para cada tarjeta y crédito. Devuelve cuántos snapshots."""
    fecha = fecha or timezone.localdate()
    n = 0
    for t in deuda.tarjetas:
        producto = _resolver_producto(banco, t.tarjeta_id, "TDC", nombre_hint=t.nombre)
        if t.tiene_cupo_usd and not producto.tiene_cupo_usd:
            producto.tiene_cupo_usd = True
            producto.save(update_fields=["tiene_cupo_usd"])
        SaldoSnapshot.objects.update_or_create(
            producto=producto,
            fecha=fecha,
            defaults=dict(
                user=user,
                deuda_facturada_clp=t.deuda_facturada_clp,
                deuda_no_facturada_clp=t.deuda_no_facturada_clp,
                cupo_total_clp=t.cupo_total_clp,
                cupo_disponible_clp=t.cupo_disponible_clp,
                cupo_usd=t.cupo_usd,
                cupo_usd_disponible=t.cupo_usd_disponible,
                deuda_usd=t.deuda_usd,
                tipo_cambio_banco=t.tipo_cambio_banco,
            ),
        )
        n += 1
    uf_actual = _valor_uf()
    for c in deuda.creditos:
        producto = _resolver_producto(banco, None, "CREDITO_HIPOTECARIO", nombre_hint=c.nombre)
        # hipotecarios en UF: el saldo viene en UF; el CLP se deriva con el TC del día
        saldo_clp = c.saldo_insoluto
        if c.moneda == "UF" and c.saldo_insoluto_uf and uf_actual:
            saldo_clp = (c.saldo_insoluto_uf * uf_actual).quantize(Decimal("1"))
        SaldoSnapshot.objects.update_or_create(
            producto=producto,
            fecha=fecha,
            defaults=dict(
                user=user,
                saldo_insoluto=saldo_clp,
                saldo_insoluto_uf=c.saldo_insoluto_uf,
                proxima_cuota=c.proxima_cuota_clp,
                proxima_cuota_uf=c.proxima_cuota_uf,
                fecha_proximo_pago=c.fecha_proximo_pago,
            ),
        )
        n += 1
    return n


def _valor_uf() -> Optional[Decimal]:
    """Último valor de la UF en CLP desde configuracion.TipoCambio (para convertir saldos)."""
    try:
        from configuracion.models import TipoCambio

        tc = TipoCambio.objects.order_by("-fecha").first()
        return tc.uf if tc and tc.uf else None
    except Exception:
        return None
