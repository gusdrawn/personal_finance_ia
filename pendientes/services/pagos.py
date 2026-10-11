from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from banca.models import CategoriaTematica, MovimientoBancario
from gastos.models import MovimientoManual

from pendientes.models import AsignacionPagoPendiente, OcurrenciaPendiente


@transaction.atomic
def vincular_pago_aseo(importacion, movimiento):
    """El certificado respalda un cargo existente; no añade otro gasto."""
    from banca.models import ImportacionEstadoCuenta
    from gastos.services import monto_facturado_clp

    importacion = ImportacionEstadoCuenta.objects.select_for_update().get(pk=importacion.pk)
    if importacion.tipo_documento != "PAGO_ASEO" or importacion.estado != "PROCESADO":
        raise ValueError("Selecciona un certificado de pago de Aseo procesado.")
    if movimiento is None:
        importacion.movimiento_pago = None
        importacion.save(update_fields=["movimiento_pago"])
        return importacion
    movimiento = MovimientoBancario.objects.select_for_update().get(pk=movimiento.pk)
    if movimiento.user_id != importacion.user_id or movimiento.producto.user_id != importacion.user_id:
        raise ValueError("El comprobante y el movimiento deben pertenecer al usuario.")
    total, estimado = monto_facturado_clp(importacion)
    if importacion.moneda != "CLP" or movimiento.moneda != "CLP" or estimado or total <= 0 or movimiento.monto != total or movimiento.monto_clp != total:
        raise ValueError("El cargo debe coincidir con el importe total del certificado en CLP.")
    if movimiento.por_revisar or movimiento.estado == "DESCARTADO_CIERRE" or movimiento.naturaleza != "OPERACIONAL":
        raise ValueError("Confirma primero que el movimiento es un pago operacional vigente.")
    if movimiento.prestamo_id or movimiento.detalles_cuentas_por_cobrar.filter(vigente=True).exists():
        raise ValueError("El cargo ya corresponde a otra fuente financiera.")
    if ImportacionEstadoCuenta.objects.filter(movimiento_pago=movimiento).exclude(pk=importacion.pk).exists():
        raise ValueError("Este cargo ya respalda otro certificado.")
    importacion.movimiento_pago = movimiento
    importacion.save(update_fields=["movimiento_pago"])
    movimiento.categoria = _categoria_pago(importacion.user, "Aseo")
    movimiento.categoria_es_manual = True
    movimiento.fuente_clasificacion = "MANUAL"
    movimiento.esencialidad = "FIJO"
    movimiento.save(update_fields=["categoria", "categoria_es_manual", "fuente_clasificacion", "esencialidad", "updated_at"])
    return importacion


def _categoria_pago(user, nombre):
    categoria, _ = CategoriaTematica.objects.get_or_create(
        user=user,
        nombre=nombre,
        defaults={"aplica_a": "EGRESO", "origen": "SISTEMA", "esencialidad_defecto": "FIJO"},
    )
    return categoria


@transaction.atomic
def resolver_ocurrencia(
    ocurrencia: OcurrenciaPendiente,
    estado: str = "COMPLETADA",
    *,
    monto: Decimal | str | None = None,
    fecha_real: date | None = None,
    movimiento_bancario: MovimientoBancario | None = None,
    nota: str = "",
    referencia_pago: str = "",
) -> OcurrenciaPendiente:
    """Resolve an occurrence and create at most one manual movement for it."""
    if estado in {"SIN_CARTOLA", "SIN_MOVIMIENTOS", "NO_APLICA", "INCLUIDA_EN_OTRO_PAGO"}:
        ocurrencia.estado = estado
        ocurrencia.completada_at = timezone.now()
        ocurrencia.nota_resolucion = nota
        ocurrencia.save(update_fields=["estado", "completada_at", "nota_resolucion", "actualizada_at"])
        return ocurrencia

    if estado != "COMPLETADA":
        raise ValueError("Estado de resolución inválido.")
    if ocurrencia.asignaciones_pago.exists():
        ocurrencia.estado = "COMPLETADA"
        ocurrencia.completada_at = ocurrencia.completada_at or timezone.now()
        ocurrencia.save(update_fields=["estado", "completada_at", "actualizada_at"])
        return ocurrencia

    valor = Decimal(str(monto or ocurrencia.monto_esperado or 0))
    if valor <= 0 and ocurrencia.regla.contabiliza_gasto:
        raise ValueError("Indica un monto para registrar el pago.")
    fecha = fecha_real or date.today()
    user = ocurrencia.regla.user
    if movimiento_bancario:
        movimiento_bancario = MovimientoBancario.objects.select_for_update().get(
            pk=movimiento_bancario.pk
        )
        if movimiento_bancario.user_id != user.id:
            raise ValueError("El movimiento no pertenece al usuario.")
        asignado = AsignacionPagoPendiente.objects.filter(
            movimiento_bancario=movimiento_bancario
        ).aggregate(total=Sum("monto_asignado"))["total"] or Decimal("0")
        if asignado + valor > abs(movimiento_bancario.monto_clp):
            raise ValueError("La suma asignada supera el monto del movimiento bancario.")
        AsignacionPagoPendiente.objects.create(
            ocurrencia=ocurrencia,
            movimiento_bancario=movimiento_bancario,
            monto_asignado=valor,
            referencia_pago=referencia_pago,
        )
    elif ocurrencia.regla.contabiliza_gasto and valor > 0:
        departamento = ocurrencia.regla.departamento
        nombre = ocurrencia.regla.nombre.lower()
        categoria_nombre = "Contribuciones" if "contrib" in nombre else "Aseo" if "aseo" in nombre else "Obligaciones"
        movimiento_manual = MovimientoManual.objects.create(
            user=user,
            fecha=fecha,
            periodo=fecha.replace(day=1),
            direccion="EGRESO",
            monto=valor,
            moneda="CLP",
            monto_clp=valor,
            categoria=_categoria_pago(user, categoria_nombre),
            departamento=departamento,
            naturaleza="OPERACIONAL",
            descripcion=ocurrencia.regla.nombre,
            notas=nota or "Creado desde pendientes.",
        )
        AsignacionPagoPendiente.objects.create(
            ocurrencia=ocurrencia,
            movimiento_manual=movimiento_manual,
            monto_asignado=valor,
            referencia_pago=referencia_pago,
        )
        from gastos.conciliaciones import conciliar_movimientos_manuales

        conciliar_movimientos_manuales(user)
    ocurrencia.estado = "COMPLETADA"
    ocurrencia.fecha_real = fecha
    ocurrencia.monto_real = valor if valor > 0 else None
    ocurrencia.completada_at = timezone.now()
    ocurrencia.nota_resolucion = nota
    ocurrencia.save(update_fields=["estado", "fecha_real", "monto_real", "completada_at", "nota_resolucion", "actualizada_at"])
    return ocurrencia
