"""
Capa de conclusiones (ver §14.5): tendencias, suscripciones, intereses, FIJO vs OCIO y
monto a pagar por tarjeta. Todo derivado de los movimientos; nada se carga a mano.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Sum

from banca.models import MovimientoBancario, SaldoSnapshot


def _operacionales(user):
    return MovimientoBancario.objects.filter(user=user, naturaleza="OPERACIONAL", por_revisar=False)


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
        SaldoSnapshot.objects.filter(user=user, deuda_facturada_clp__isnull=False)
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
                "fecha": snap.fecha,
            })
    return out
