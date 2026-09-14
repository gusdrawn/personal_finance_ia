"""Ciclo de vida de compras Apple Pay provisionales frente al estado de cuenta."""

from __future__ import annotations

from difflib import SequenceMatcher
from decimal import Decimal

from django.db import transaction

from banca.models import MovimientoBancario
from banca.services.clasificacion_ia import normalizar_glosa


def _similitud(a: str, b: str) -> float:
    return SequenceMatcher(None, normalizar_glosa(a), normalizar_glosa(b)).ratio()


def _candidatos(provisional: MovimientoBancario, confirmados: list[MovimientoBancario]):
    return [
        mov
        for mov in confirmados
        if mov.producto_id == provisional.producto_id
        and mov.moneda == provisional.moneda
        and Decimal(mov.monto) == Decimal(provisional.monto)
        and abs((mov.fecha - provisional.fecha).days) <= 3
    ]


def _elegir(provisional: MovimientoBancario, confirmados: list[MovimientoBancario]):
    candidatos = _candidatos(provisional, confirmados)
    if len(candidatos) == 1:
        return candidatos[0], False
    if not candidatos:
        return None, False
    puntuados = sorted(
        ((_similitud(provisional.descripcion, mov.descripcion), mov) for mov in candidatos),
        key=lambda item: item[0],
        reverse=True,
    )
    mejor, segundo = puntuados[0], puntuados[1]
    if mejor[0] >= 0.60 and mejor[0] - segundo[0] >= 0.15:
        return mejor[1], False
    return None, True


def _confirmar_en_misma_fila(
    provisional: MovimientoBancario,
    confirmado: MovimientoBancario,
    importacion,
) -> MovimientoBancario:
    """Conserva la identidad Apple Pay y reemplaza solo los hechos bancarios del PDF."""
    importaciones = list(confirmado.importaciones.all())
    datos_pdf = {
        "producto": confirmado.producto,
        "fecha": confirmado.fecha,
        "descripcion": confirmado.descripcion,
        "monto": confirmado.monto,
        "moneda": confirmado.moneda,
        "monto_clp": confirmado.monto_clp,
        "tipo_cambio_banco": confirmado.tipo_cambio_banco,
        "cuota_actual": confirmado.cuota_actual,
        "cuota_total": confirmado.cuota_total,
        "periodo_factura": confirmado.periodo_factura,
        "hash_externo": confirmado.hash_externo,
    }
    confirmado.delete()
    for campo, valor in datos_pdf.items():
        setattr(provisional, campo, valor)
    provisional.estado = "FACTURADO"
    provisional.origen = "PDF"
    provisional.provisional_hasta = None
    provisional.cerrado_por_importacion = importacion
    provisional.motivo_cierre = "CONFIRMADO_PDF"
    provisional.save()
    provisional.importaciones.add(*importaciones, importacion)
    return provisional


@transaction.atomic
def cerrar_periodo_apple_pay(
    user,
    producto,
    periodo_inicio,
    periodo_fin,
    movimientos_confirmados: list[MovimientoBancario],
    importacion,
) -> dict:
    """El PDF cierra provisionales del producto dentro del rango explícito."""
    if not periodo_inicio or not periodo_fin:
        return {"confirmados": 0, "descartados": 0, "ambiguos": 0, "sin_periodo": True}

    provisionales = list(
        MovimientoBancario.objects.select_for_update()
        .filter(
            user=user,
            producto=producto,
            origen="APPLE_PAY",
            estado="PROVISIONAL",
            fecha__range=(periodo_inicio, periodo_fin),
        )
        .order_by("fecha", "id")
    )
    disponibles = list(movimientos_confirmados)
    confirmados = descartados = ambiguos = 0

    for provisional in provisionales:
        confirmado, ambiguo = _elegir(provisional, disponibles)
        if confirmado:
            confirmado_id = confirmado.pk
            _confirmar_en_misma_fila(provisional, confirmado, importacion)
            confirmados += 1
            disponibles = [m for m in disponibles if m.pk != confirmado_id]
            continue
        elif ambiguo:
            provisional.estado = "DESCARTADO_CIERRE"
            provisional.motivo_cierre = "AMBIGUO_PDF"
            ambiguos += 1
        else:
            provisional.estado = "DESCARTADO_CIERRE"
            provisional.motivo_cierre = "NO_APARECE_EN_PDF"
            descartados += 1
        provisional.cerrado_por_importacion = importacion
        provisional.save(
            update_fields=[
                "estado",
                "motivo_cierre",
                "cerrado_por_importacion",
                "updated_at",
            ]
        )

    return {
        "confirmados": confirmados,
        "descartados": descartados,
        "ambiguos": ambiguos,
        "sin_periodo": False,
    }
