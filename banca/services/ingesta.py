"""
Ingesta idempotente: objetos normalizados de bank_sync -> ORM de banca.

Clave anti-duplicación: el upsert es por `user + hash_externo` (huella estable entre
NO_FACTURADO y FACTURADO). Si el movimiento ya existe, se ACTUALIZA su estado/período;
no se inserta de nuevo. La clasificación manual del usuario nunca se pisa.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional
from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from bank_sync.models import DeudaResumen, EstadoCuenta, MovimientoNormalizado, _norm
from banca.models import MovimientoBancario, SaldoSnapshot


def _resolver_producto(user, banco, tarjeta_id: Optional[str], tipo: str = "TDC", nombre_hint: str = ""):
    """Mapea un identificador de tarjeta/crédito a un configuracion.Producto (get_or_create)."""
    from configuracion.models import Producto
    from configuracion.services.productos import producto_canonico

    tid = (tarjeta_id or "").strip()
    if tid:
        prod = (
            Producto.objects.filter(user=user, banco=banco, tipo=tipo, ultimos4=tid[-4:]).first()
            or Producto.objects.filter(user=user, banco=banco, tipo=tipo, nombre__icontains=tid).first()
        )
        if prod:
            return producto_canonico(prod)
        nombre = nombre_hint or f"{'TDC' if tipo == 'TDC' else tipo} *{tid}"
        prod, _ = Producto.objects.get_or_create(
            user=user, banco=banco, nombre=nombre,
            defaults={"tipo": tipo, "tiene_cupo_usd": False, "ultimos4": tid[-4:], "origen": "CONECTOR"}
        )
        return producto_canonico(prod)
    # sin identificador: producto genérico por tipo
    prod, _ = Producto.objects.get_or_create(
        user=user, banco=banco, nombre=nombre_hint or f"{tipo} (sin asignar)",
        defaults={"tipo": tipo, "origen": "CONECTOR"}
    )
    return producto_canonico(prod)


def _a_clp(mov: MovimientoNormalizado, tipo_cambio_usd: Optional[Decimal]) -> tuple[Decimal, Optional[Decimal]]:
    if mov.moneda == "USD":
        tc = tipo_cambio_usd
        if tc:
            return (mov.monto * tc, tc)
        return (Decimal("0"), None)  # se completará cuando haya TC del banco
    return (mov.monto, None)


@transaction.atomic
def ingestar_movimientos(
    user,
    banco,
    movimientos: list[MovimientoNormalizado],
    *,
    tipo_cambio_usd: Optional[Decimal] = None,
    origen: str = "CONECTOR",
    tipo_producto: str = "TDC",
    nombre_producto: str = "",
) -> dict:
    """Inserta o actualiza movimientos. Devuelve {'nuevos': n, 'actualizados': m, 'ids': [...]}."""
    nuevos, actualizados, ids = 0, 0, []
    ocurrencias = defaultdict(int)
    usados = set()
    for mov in movimientos:
        huella_legacy = mov.hash_externo()
        producto = _resolver_producto(
            user,
            banco,
            mov.tarjeta_id,
            tipo_producto,
            nombre_hint=nombre_producto,
        )
        # Serializa importaciones concurrentes del mismo producto. Sin ID del
        # proveedor, una corrida se trata como un multiconjunto de operaciones.
        type(producto).objects.select_for_update().get(pk=producto.pk, user=user)
        clave = (producto.pk, huella_legacy)
        ocurrencias[clave] += 1
        h = mov.hash_externo(f"banco:{banco.pk}:producto:{producto.pk}", ocurrencias[clave])
        monto_clp, tc = _a_clp(mov, tipo_cambio_usd)

        existente = MovimientoBancario.objects.filter(user=user, hash_externo=h).first()
        if existente is None and mov.external_id:
            existente = MovimientoBancario.objects.filter(
                user=user, producto=producto, referencia_externa=mov.external_id,
            ).first()
        if existente is not None and existente.pk in usados and not mov.external_id:
            existente = None
        if existente is None:
            # Upgrade the old user-wide hash only when it belongs to the same
            # resolved product. Identical purchases on another product are distinct.
            legado = MovimientoBancario.objects.filter(
                user=user, hash_externo=huella_legacy, producto=producto
            ).exclude(pk__in=usados).first()
            existente = legado
        if existente is None:
            # Tras consolidar productos, la huella antigua puede contener el ID
            # anterior. Reconoce el contenido dentro del producto ya resuelto;
            # cada fila sólo se consume una vez por corrida.
            candidatos = MovimientoBancario.objects.filter(
                user=user, producto=producto, fecha=mov.fecha, monto=mov.monto,
                moneda=mov.moneda, cuota_actual=mov.cuota_actual,
                cuota_total=mov.cuota_total,
            ).exclude(pk__in=usados).order_by("pk")
            if mov.external_id:
                candidatos = candidatos.filter(referencia_externa="")
            existente = next((item for item in candidatos if _norm(item.descripcion) == _norm(mov.descripcion)), None)
        if existente:
            # solo avanzar el ciclo de vida / período; respetar lo manual
            cambios = []
            if mov.external_id and existente.referencia_externa != mov.external_id:
                existente.referencia_externa = mov.external_id
                cambios.append("referencia_externa")
            if (mov.external_id or not existente.referencia_externa) and existente.hash_externo != h and not MovimientoBancario.objects.filter(user=user, hash_externo=h).exclude(pk=existente.pk).exists():
                existente.hash_externo = h
                cambios.append("hash_externo")
            if mov.estado == "FACTURADO" and existente.estado == "NO_FACTURADO":
                existente.estado = "FACTURADO"
                cambios.append("estado")
            if origen == "PDF" and existente.origen != "PDF":
                existente.origen = "PDF"
                cambios.append("origen")
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
            usados.add(existente.id)
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
            origen=origen,
            periodo_factura=mov.periodo_factura,
            hash_externo=h,
            referencia_externa=mov.external_id,
        )
        nuevos += 1
        ids.append(nuevo.id)
        usados.add(nuevo.id)
    from gastos.conciliaciones import conciliar_movimientos_manuales

    conciliar_movimientos_manuales(user)
    return {"nuevos": nuevos, "actualizados": actualizados, "ids": ids}


def ingestar_estado_cuenta(user, banco, estados: list[EstadoCuenta]) -> dict:
    """Ingesta movimientos facturados de uno o más estados de cuenta."""
    total = {"nuevos": 0, "actualizados": 0, "ids": []}
    for ec in estados:
        res = ingestar_movimientos(
            user,
            banco,
            ec.movimientos,
            tipo_cambio_usd=ec.tipo_cambio_facturacion,
            origen="PDF",
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
        producto = _resolver_producto(user, banco, t.tarjeta_id, "TDC", nombre_hint=t.nombre)
        if t.tiene_cupo_usd and not producto.tiene_cupo_usd:
            producto.tiene_cupo_usd = True
            producto.save(update_fields=["tiene_cupo_usd"])
        SaldoSnapshot.objects.update_or_create(
            user=user,
            producto=producto,
            fecha=fecha,
            defaults=dict(
                deuda_facturada_clp=t.deuda_facturada_clp,
                deuda_no_facturada_clp=t.deuda_no_facturada_clp,
                cupo_total_clp=t.cupo_total_clp,
                cupo_utilizado_clp=t.cupo_utilizado_clp,
                cupo_disponible_clp=t.cupo_disponible_clp,
                cupo_avance_disponible_clp=t.cupo_avance_disponible_clp,
                fecha_vencimiento=t.fecha_vencimiento,
                cupo_usd=t.cupo_usd,
                cupo_usd_disponible=t.cupo_usd_disponible,
                deuda_usd=t.deuda_usd,
                tipo_cambio_banco=t.tipo_cambio_banco,
            ),
        )
        n += 1
    uf_actual = _valor_uf()
    for c in deuda.creditos:
        producto = _resolver_producto(user, banco, None, "CREDITO_HIPOTECARIO", nombre_hint=c.nombre)
        # hipotecarios en UF: el saldo viene en UF; el CLP se deriva con el TC del día
        saldo_clp = c.saldo_insoluto
        if c.moneda == "UF" and c.saldo_insoluto_uf and uf_actual:
            saldo_clp = (c.saldo_insoluto_uf * uf_actual).quantize(Decimal("1"))
        SaldoSnapshot.objects.update_or_create(
            user=user,
            producto=producto,
            fecha=fecha,
            defaults=dict(
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
