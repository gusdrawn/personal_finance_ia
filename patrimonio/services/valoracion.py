"""Valuación consistente de activos y pasivos para reportes patrimoniales."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Exists, OuterRef, Q

from banca.models import SaldoSnapshot
from configuracion.services.tipos_cambio import tipo_cambio_para
from departamentos.models import CreditoHipotecario, Departamento
from patrimonio.models import Activo, Pasivo


ZERO = Decimal("0")
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Valoracion:
    clp: Decimal
    usd: Decimal
    requiere_revision: bool = False
    falta_tasa: bool = False


def valorar_saldo(saldo, tipo_cambio=None) -> Valoracion:
    """Return cached CLP/USD values, recalculating from a known source balance."""
    clp_cache = Decimal(saldo.monto_clp or ZERO)
    usd_cache = Decimal(saldo.monto_usd or ZERO)
    monto = saldo.monto_original
    moneda = saldo.moneda_original

    # Legacy records with both currencies have no reliable way to tell whether
    # those values are equivalents or separate balances. Keep both visible and
    # preserve the CLP reporting total until the user reviews the source currency.
    if monto is None or moneda not in {"CLP", "USD"}:
        return Valoracion(clp_cache, usd_cache, bool(saldo.requiere_revision_moneda))

    monto = Decimal(monto)
    tasa = getattr(tipo_cambio, "dolar", None)
    if moneda == "CLP":
        clp = monto
        usd = (monto / tasa).quantize(CENT, rounding=ROUND_HALF_UP) if tasa else usd_cache
    else:
        usd = monto
        clp = (monto * tasa).quantize(CENT, rounding=ROUND_HALF_UP) if tasa else clp_cache
    return Valoracion(clp, usd, False, not bool(tasa))


def resumen_patrimonio(user, fecha: date | None = None) -> dict:
    """Shared CLP/USD totals used by dashboards, detail pages, and snapshots."""
    tasa = tipo_cambio_para(fecha)
    activos = list(
        Activo.objects.filter(user=user, activo=True).exclude(tipo="DEPARTAMENTO")
        .select_related('prestamo_recuperable')
    )
    snapshot_producto = SaldoSnapshot.objects.filter(
        user=user, producto_id=OuterRef("producto_id"), fecha__lte=fecha or date.today(),
    ).filter(Q(saldo_insoluto__isnull=False) | Q(deuda_facturada_clp__isnull=False) | Q(deuda_no_facturada_clp__isnull=False) | Q(deuda_usd__isnull=False))
    pasivos = list(
        Pasivo.objects.filter(user=user, activo=True)
        .filter(Q(producto__isnull=True) | Q(producto__user=user, producto__contabilizar=True))
        .annotate(tiene_snapshot_banca=Exists(snapshot_producto))
        # Keep an older manual amount as a fallback, but do not add it again once
        # the same linked product has bank history and a generated liability.
        .exclude(
            producto__isnull=False,
            sincronizado_banca=False,
            tiene_snapshot_banca=True,
        )
    )
    # Inactivation preserves the property and documents, but excludes its value.
    # A remaining mortgage continues to be an independent obligation.
    departamentos = list(Departamento.objects.filter(user=user, activo=True))
    productos_con_pasivo = {item.producto_id for item in pasivos if item.producto_id}
    productos_con_snapshot = set(SaldoSnapshot.objects.filter(
        user=user, fecha__lte=fecha or date.today(),
    ).filter(Q(saldo_insoluto__isnull=False) | Q(deuda_facturada_clp__isnull=False) | Q(deuda_no_facturada_clp__isnull=False) | Q(deuda_usd__isnull=False)).values_list('producto_id', flat=True))
    hipotecas_manuales = []
    for credito in CreditoHipotecario.objects.filter(
        departamento__user=user, activo=True,
    ).select_related('departamento', 'producto__banco', 'banco'):
        if credito.producto_id and (
            credito.producto.user_id != user.pk or not credito.producto.contabilizar
        ):
            continue
        # A bank balance, including an explicit zero, owns this product's debt.
        if credito.producto_id in productos_con_pasivo | productos_con_snapshot:
            continue
        saldo_clp = credito.saldo_actual_uf * tasa.uf if tasa and tasa.uf else ZERO
        saldo_usd = (saldo_clp / tasa.dolar).quantize(CENT) if tasa and tasa.dolar else ZERO
        # Project the mortgage source for display; do not persist a second balance.
        pasivo = Pasivo(
            id=f'hipoteca:{credito.pk}', user=user, nombre=f'Hipoteca {credito.departamento.codigo}',
            tipo='CREDITO_HIPOTECARIO', producto=credito.producto,
            monto_clp=saldo_clp, monto_usd=saldo_usd, updated_at=credito.updated_at,
        )
        pasivo.credito_origen_id = credito.pk
        pasivo.saldo_uf = credito.saldo_actual_uf
        hipotecas_manuales.append(pasivo)
    pasivos.extend(hipotecas_manuales)

    for item in activos:
        prestamo = getattr(item, 'prestamo_recuperable', None)
        if prestamo and prestamo.user_id == user.pk:
            item.monto_usd = ZERO
            item.asignar_saldo_original(prestamo.saldo_recuperable(fecha), 'CLP', tasa)
    valores_activos = [(item, valorar_saldo(item, tasa)) for item in activos]
    valores_pasivos = [(item, valorar_saldo(item, tasa)) for item in pasivos]
    clp_departamentos = (
        sum((item.valor_actual_uf * tasa.uf for item in departamentos), ZERO)
        if tasa and tasa.uf
        else ZERO
    )
    usd_departamentos = (
        (clp_departamentos / tasa.dolar).quantize(CENT, rounding=ROUND_HALF_UP)
        if tasa and tasa.dolar
        else ZERO
    )

    activos_clp = sum((value.clp for _, value in valores_activos), ZERO) + clp_departamentos
    activos_usd = sum((value.usd for _, value in valores_activos), ZERO) + usd_departamentos
    pasivos_clp = sum((value.clp for _, value in valores_pasivos), ZERO)
    pasivos_usd = sum((value.usd for _, value in valores_pasivos), ZERO)
    liquidos_clp = sum(
        (value.clp for item, value in valores_activos if item.es_liquido), ZERO
    )

    revision = sum(value.requiere_revision for _, value in valores_activos + valores_pasivos)
    falta_uf = bool((departamentos or hipotecas_manuales) and not (tasa and tasa.uf))
    falta_usd = bool(
        not (tasa and tasa.dolar)
        and any(
            item.monto_original is not None and Decimal(item.monto_original) != ZERO
            for item in activos + pasivos
        )
        or (not (tasa and tasa.dolar) and (departamentos or hipotecas_manuales))
    )
    return {
        "tipo_cambio": tasa,
        "activos": activos,
        "pasivos": pasivos,
        "departamentos": departamentos,
        "valores_activos": dict((item.pk, value) for item, value in valores_activos),
        "valores_pasivos": dict((item.pk, value) for item, value in valores_pasivos),
        "activos_total_clp": activos_clp,
        "activos_total_usd": activos_usd,
        "pasivos_total_clp": pasivos_clp,
        "pasivos_total_usd": pasivos_usd,
        "patrimonio_neto_clp": activos_clp - pasivos_clp,
        "patrimonio_neto_usd": activos_usd - pasivos_usd,
        "activos_liquidos_clp": liquidos_clp,
        "activos_no_liquidos_clp": activos_clp - liquidos_clp,
        "liquidez_pct": (liquidos_clp / activos_clp * 100) if activos_clp > ZERO else ZERO,
        "filas_por_revisar_moneda": revision,
        "falta_tasa_uf": falta_uf,
        "falta_tasa_usd": falta_usd,
    }
