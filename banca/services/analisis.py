"""
Capa de conclusiones (ver §14.5): tendencias, suscripciones, intereses, FIJO vs OCIO y
monto a pagar por tarjeta. Todo derivado de los movimientos; nada se carga a mano.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Q, Sum

from banca.models import MovimientoBancario, SaldoSnapshot


def _operacionales(user):
    return MovimientoBancario.objects.filter(
        user=user, producto__user=user, producto__contabilizar=True, naturaleza="OPERACIONAL", por_revisar=False
    ).exclude(estado="DESCARTADO_CIERRE")


def fijo_vs_ocio(user, year: int, month: int) -> dict:
    movs = _operacionales(user).filter(fecha__year=year, fecha__month=month, monto_clp__gt=0)
    fijo = sum((m.monto_clp for m in movs.filter(esencialidad="FIJO").only("monto_clp")), Decimal("0"))
    ocio = sum((m.monto_clp for m in movs.filter(esencialidad="OCIO").only("monto_clp")), Decimal("0"))
    return {"fijo": fijo, "ocio": ocio, "total": fijo + ocio}


def por_categoria(user, year: int, month: int) -> list[dict]:
    movs = (
        _operacionales(user)
        .filter(fecha__year=year, fecha__month=month, monto_clp__gt=0, categoria__isnull=False)
        .values("categoria__nombre", "categoria__esencialidad_defecto")
        .annotate(total=Sum("monto_clp"), n=Count("id"))
        .order_by("-total")
    )
    return [
        {
            "categoria": r["categoria__nombre"],
            "esencialidad": r["categoria__esencialidad_defecto"],
            "total": r["total"],
            "n": r["n"],
        }
        for r in movs
    ]


def total_intereses(user, year: int, month: int) -> Decimal:
    movs = _operacionales(user).filter(
        fecha__year=year, fecha__month=month, monto_clp__gt=0,
        categoria__es_interes_o_costo_financiero=True,
    )
    return sum((m.monto_clp for m in movs.only("monto_clp")), Decimal("0"))


def detectar_suscripciones(user, meses_min: int = 2) -> list[dict]:
    """Comercios con cargos recurrentes (misma glosa en >= meses_min meses distintos)."""
    movs = _operacionales(user).filter(monto_clp__gt=0).only("descripcion", "fecha", "monto_clp")
    por_comercio: dict[str, set] = defaultdict(set)
    monto_comercio: dict[str, list] = defaultdict(list)
    for m in movs:
        clave = (m.descripcion or "").strip().lower()[:40]
        por_comercio[clave].add((m.fecha.year, m.fecha.month))
        monto_comercio[clave].append(m.monto_clp)
    out = []
    for clave, meses in por_comercio.items():
        if len(meses) >= meses_min and clave:
            montos = monto_comercio[clave]
            out.append({
                "comercio": clave,
                "meses": len(meses),
                "monto_promedio": (sum(montos) / len(montos)).quantize(Decimal("1")),
            })
    return sorted(out, key=lambda x: x["meses"], reverse=True)


def monto_a_pagar_tdc(user) -> list[dict]:
    """Último saldo facturado por tarjeta (lo que hay que pagar)."""
    out = []
    productos = (
        SaldoSnapshot.objects.filter(user=user, producto__user=user, producto__contabilizar=True)
        .filter(Q(deuda_facturada_clp__isnull=False) | Q(deuda_usd__isnull=False))
        .order_by()
        .values_list("producto", flat=True)
        .distinct()
    )
    for prod_id in productos:
        snap = (
            SaldoSnapshot.objects.filter(user=user, producto_id=prod_id)
            .order_by("-fecha")
            .select_related("producto")
            .first()
        )
        if snap:
            out.append({
                "producto": str(snap.producto),
                "facturado_clp": snap.deuda_facturada_clp or Decimal("0"),
                "no_facturado_clp": snap.deuda_no_facturada_clp or Decimal("0"),
                "facturado_usd": snap.deuda_usd or Decimal("0"),
                "fecha": snap.fecha,
            })
    return out


def cupos_tdc(user) -> dict:
    """Último cupo conocido por tarjeta y resumen total del usuario.

    Se consulta solo el snapshot más reciente que realmente contiene cupos. Esto
    permite que una importación antigua que solo traía deuda no oculte el último
    cupo leído desde un estado de cuenta posterior.
    """
    snapshots_con_cupo = (
        SaldoSnapshot.objects.filter(user=user, producto__user=user, producto__contabilizar=True, producto__tipo="TDC")
        .exclude(cupo_total_clp__isnull=True, cupo_disponible_clp__isnull=True)
        .order_by()
    )
    producto_ids = snapshots_con_cupo.values_list("producto_id", flat=True).distinct()
    tarjetas = []
    for producto_id in producto_ids:
        snapshot = (
            snapshots_con_cupo.filter(producto_id=producto_id)
            .select_related("producto", "producto__banco")
            .order_by("-fecha", "-id")
            .first()
        )
        if not snapshot:
            continue
        total = snapshot.cupo_total_clp
        disponible = snapshot.cupo_disponible_clp
        utilizado = snapshot.cupo_utilizado_clp
        if utilizado is None and total is not None and disponible is not None:
            utilizado = max(total - disponible, Decimal("0"))
        tarjetas.append(
            {
                "producto": snapshot.producto,
                "banco": snapshot.producto.banco,
                "cupo_total_clp": total,
                "cupo_utilizado_clp": utilizado,
                "cupo_disponible_clp": disponible,
                "cupo_avance_disponible_clp": snapshot.cupo_avance_disponible_clp,
                "fecha": snapshot.fecha,
            }
        )

    def suma(campo: str) -> Decimal:
        return sum(
            (tarjeta[campo] or Decimal("0") for tarjeta in tarjetas),
            Decimal("0"),
        )

    return {
        "tarjetas": tarjetas,
        "total_clp": suma("cupo_total_clp"),
        "utilizado_clp": suma("cupo_utilizado_clp"),
        "disponible_clp": suma("cupo_disponible_clp"),
        "fecha": max((tarjeta["fecha"] for tarjeta in tarjetas), default=None),
    }


def vencimientos_tdc(user, year: int, month: int) -> list[dict]:
    """Vencimientos de tarjetas del mes, derivados de la fecha «Pagar hasta»."""
    snapshots = (
        SaldoSnapshot.objects.filter(
            user=user,
            producto__user=user, producto__contabilizar=True,
            producto__tipo="TDC",
            fecha_vencimiento__year=year,
            fecha_vencimiento__month=month,
        )
        .select_related("producto", "producto__banco")
        .order_by("producto_id", "-fecha", "-id")
    )
    vencimientos = []
    vistos: set[tuple[int, object]] = set()
    for snapshot in snapshots:
        fecha = snapshot.fecha_vencimiento
        clave = (snapshot.producto_id, fecha)
        if clave in vistos:
            continue
        vistos.add(clave)
        vencimientos.append(
            {
                "fecha": fecha,
                "dia_cobro": fecha.day,
                "nombre": f"{snapshot.producto.banco.nombre} · {snapshot.producto.nombre}",
                "banco": snapshot.producto.banco.nombre,
                "producto": snapshot.producto.nombre,
                "monto": snapshot.deuda_facturada_clp or Decimal("0"),
                "monto_clp": snapshot.deuda_facturada_clp,
                "monto_usd": snapshot.deuda_usd,
                "tipo": "tarjeta",
            }
        )
    return sorted(vencimientos, key=lambda item: (item["fecha"], item["nombre"]))
