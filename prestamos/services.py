"""Saldo patrimonial de préstamos, vinculado por identidad y no por nombre."""

from django.db import transaction
from configuracion.services.tipos_cambio import tipo_cambio_para
from patrimonio.models import Activo
from prestamos.models import Prestamo


@transaction.atomic
def sincronizar_activo_prestamo(prestamo):
    prestamo = Prestamo.objects.select_for_update().select_related('activo_patrimonial').get(pk=prestamo.pk)
    activo = prestamo.activo_patrimonial
    if activo and activo.user_id != prestamo.user_id:
        raise ValueError('El activo y el préstamo deben pertenecer al mismo usuario.')
    if prestamo.tipo != 'TERCEROS':
        if activo:
            activo.activo = False
            activo.save(update_fields=['activo', 'updated_at'])
        return activo
    if activo is None:
        legacy = Activo.objects.filter(
            user_id=prestamo.user_id, tipo='PRESTAMO_DADO',
            nombre=f'Bicicleta - {prestamo.nombre}',
            notas=f'Auto-generado desde Bicicleta: {prestamo.nombre}',
            prestamo_recuperable__isnull=True,
        )
        if legacy.exists():
            # Migration leaves ambiguous identities untouched; an edit must not
            # create another asset on top of that unresolved legacy balance.
            legacy.update(requiere_revision_fuente=True)
            return None
        activo = Activo.objects.create(
            user_id=prestamo.user_id, nombre=f'Bicicleta - {prestamo.nombre}',
            tipo='PRESTAMO_DADO', horizonte_temporal='CORTO_PLAZO', es_liquido=False,
        )
        Prestamo.objects.filter(pk=prestamo.pk).update(activo_patrimonial=activo)
    activo.nombre = f'Bicicleta - {prestamo.nombre}'
    activo.tipo = 'PRESTAMO_DADO'
    activo.horizonte_temporal = 'CORTO_PLAZO'
    activo.es_liquido = False
    activo.activo = prestamo.activo
    # Clear the former currency cache even when no reference rate is available.
    activo.monto_usd = 0
    activo.asignar_saldo_original(prestamo.saldo_recuperable(), 'CLP', tipo_cambio_para())
    activo.notas = f'Auto-generado desde Bicicleta: {prestamo.nombre}'
    activo.save()
    return activo
