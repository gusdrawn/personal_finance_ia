"""Conciliación conservadora de movimientos manuales con sus fuentes bancarias."""

from decimal import Decimal
from collections import defaultdict

from django.db import transaction

from banca.models import MovimientoBancario
from gastos.models import ConciliacionMovimientoManual, MovimientoManual


TOLERANCIA_CLP = Decimal("1")


def _coincide(manual, movimiento):
    if manual.user_id != movimiento.user_id or manual.moneda != movimiento.moneda:
        return False
    if manual.naturaleza != "OPERACIONAL":
        return False
    if manual.fecha != movimiento.fecha or movimiento.naturaleza != "OPERACIONAL":
        return False
    if movimiento.por_revisar or movimiento.estado == "DESCARTADO_CIERRE":
        return False
    signo_correcto = (
        movimiento.monto > 0
        if manual.direccion == "EGRESO"
        else movimiento.monto < 0 and movimiento.producto.tipo == "CUENTA"
    )
    tolerancia = TOLERANCIA_CLP if manual.moneda == "CLP" else Decimal("0.01")
    return signo_correcto and abs(abs(movimiento.monto) - manual.monto) <= tolerancia


@transaction.atomic
def conciliar_movimientos_manuales(user):
    """Sugiere coincidencias; sustituir un pago manual requiere confirmación."""
    conciliaciones_creadas = 0
    confirmadas = 0
    manuales = list(
        MovimientoManual.objects.filter(user=user, es_resumen_mensual=False)
        .exclude(naturaleza="TRANSFERENCIA_INTERNA")
        .order_by("fecha", "id")
    )
    movimientos = list(
        MovimientoBancario.objects.filter(user=user, naturaleza="OPERACIONAL")
        .exclude(estado="DESCARTADO_CIERRE")
        .order_by("fecha", "id")
    )
    movimientos_por_fecha_signo = defaultdict(list)
    for movimiento in movimientos:
        signo = 1 if movimiento.monto_clp > 0 else -1 if movimiento.monto_clp < 0 else 0
        if signo:
            movimientos_por_fecha_signo[(movimiento.fecha, signo)].append(movimiento)
    existentes = list(
        ConciliacionMovimientoManual.objects.filter(
            movimiento_manual__user=user,
            estado="CONFIRMADA",
            confirmada_manual=True,
        ).select_related("movimiento_manual", "movimiento_bancario")
    )

    # Preserve an explicit user choice while it still matches. Release stale
    # choices before calculating automatic candidates.
    reservados_manuales, reservados_bancarios = set(), set()
    for enlace in existentes:
        if _coincide(enlace.movimiento_manual, enlace.movimiento_bancario):
            reservados_manuales.add(enlace.movimiento_manual_id)
            reservados_bancarios.add(enlace.movimiento_bancario_id)
        else:
            enlace.estado = "OBSOLETA"
            enlace.confirmada_manual = False
            enlace.save(update_fields=["estado", "confirmada_manual", "actualizada_at"])

    ConciliacionMovimientoManual.objects.filter(
        movimiento_manual__user=user,
        estado="CONFIRMADA",
        confirmada_manual=False,
    ).update(estado="SUGERIDA", confirmada_manual=False)

    manuales_pendientes = [m for m in manuales if m.pk not in reservados_manuales]
    candidatos_por_manual = {}
    pares_vigentes = set()
    for manual in manuales_pendientes:
        signo = 1 if manual.direccion == "EGRESO" else -1
        candidatos = [
            m for m in movimientos_por_fecha_signo[(manual.fecha, signo)]
            if m.pk not in reservados_bancarios and _coincide(manual, m)
        ]
        # A user's rejection is scoped to this exact pair and should not be
        # recreated as an open suggestion on the next bank import.
        descartados = set(
            ConciliacionMovimientoManual.objects.filter(
                movimiento_manual=manual, estado="DESCARTADA"
            ).values_list("movimiento_bancario_id", flat=True)
        )
        candidatos = [m for m in candidatos if m.pk not in descartados]
        candidatos_por_manual[manual.pk] = candidatos
        for movimiento in candidatos:
            pares_vigentes.add((manual.pk, movimiento.pk))

    for manual in manuales_pendientes:
        candidatos = candidatos_por_manual[manual.pk]
        for movimiento in candidatos:
            enlace, created = ConciliacionMovimientoManual.objects.get_or_create(
                movimiento_manual=manual,
                movimiento_bancario=movimiento,
                defaults={"estado": "SUGERIDA"},
            )
            conciliaciones_creadas += int(created)
            if enlace.estado == "DESCARTADA":
                continue
            if enlace.estado == "OBSOLETA":
                enlace.estado = "SUGERIDA"
                enlace.confirmada_manual = False
                enlace.save(update_fields=["estado", "confirmada_manual", "actualizada_at"])

    for enlace in ConciliacionMovimientoManual.objects.filter(
        movimiento_manual__user=user, estado="SUGERIDA"
    ).iterator():
        if (enlace.movimiento_manual_id, enlace.movimiento_bancario_id) not in pares_vigentes:
            enlace.estado = "OBSOLETA"
            enlace.save(update_fields=["estado", "actualizada_at"])

    return {"creadas": conciliaciones_creadas, "confirmadas": confirmadas}
