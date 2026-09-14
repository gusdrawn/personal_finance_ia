"""Consolidación explícita de cuentas financieras importadas y configuradas."""

from django.db import transaction

from banca.models import ImportacionEstadoCuenta, MovimientoBancario, SaldoSnapshot
from configuracion.models import Producto
from departamentos.models import CreditoHipotecario
from patrimonio.models import Pasivo
from prestamos.models import Prestamo


@transaction.atomic
def vincular_producto(user, anterior: Producto, canonico: Producto) -> Producto:
    if anterior.user_id != user.id or canonico.user_id != user.id:
        raise ValueError("Ambos productos deben pertenecer al usuario.")
    if anterior.pk == canonico.pk:
        raise ValueError("El producto anterior y el canónico deben ser distintos.")
    if anterior.banco_id != canonico.banco_id or anterior.tipo != canonico.tipo:
        raise ValueError("Banco y tipo deben coincidir para consolidar productos.")

    MovimientoBancario.objects.filter(user=user, producto=anterior).update(producto=canonico)
    ImportacionEstadoCuenta.objects.filter(user=user, producto=anterior).update(producto=canonico)
    Pasivo.objects.filter(user=user, producto=anterior).update(producto=canonico)
    Prestamo.objects.filter(user=user, tarjeta_asociada=anterior).update(tarjeta_asociada=canonico)
    CreditoHipotecario.objects.filter(
        departamento__user=user, producto=anterior
    ).update(producto=canonico)

    for snapshot in SaldoSnapshot.objects.filter(user=user, producto=anterior):
        existente = SaldoSnapshot.objects.filter(
            user=user, producto=canonico, fecha=snapshot.fecha
        ).first()
        if not existente:
            snapshot.producto = canonico
            snapshot.save(update_fields=["producto"])
            continue
        campos = []
        for field in (
            "fecha_vencimiento", "deuda_facturada_clp", "deuda_no_facturada_clp",
            "cupo_total_clp", "cupo_disponible_clp", "cupo_utilizado_clp",
            "cupo_avance_disponible_clp", "cupo_usd", "cupo_usd_disponible",
            "deuda_usd", "tipo_cambio_banco", "saldo_insoluto", "saldo_insoluto_uf",
            "proxima_cuota", "proxima_cuota_uf", "fecha_proximo_pago",
        ):
            if getattr(existente, field) is None and getattr(snapshot, field) is not None:
                setattr(existente, field, getattr(snapshot, field))
                campos.append(field)
        if campos:
            existente.save(update_fields=campos)
        snapshot.delete()

    anterior.activo = False
    anterior.requiere_revision = False
    anterior.reemplazado_por = canonico
    anterior.save(update_fields=["activo", "requiere_revision", "reemplazado_por"])
    canonico.activo = True
    canonico.requiere_revision = False
    canonico.save(update_fields=["activo", "requiere_revision"])
    return canonico
