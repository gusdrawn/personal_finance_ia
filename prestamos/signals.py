"""Mantiene el capital recuperable al cambiar préstamos y sus devoluciones."""

from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from banca.models import MovimientoBancario
from patrimonio.models import Activo
from prestamos.models import HistorialPrestamo, Prestamo
from prestamos.services import sincronizar_activo_prestamo


@receiver(post_save, sender=Prestamo)
def sincronizar_prestamo(sender, instance, raw=False, **kwargs):
    if not raw:
        activo = sincronizar_activo_prestamo(instance)
        instance.activo_patrimonial_id = activo.pk if activo else None


@receiver(post_save, sender=HistorialPrestamo)
@receiver(post_delete, sender=HistorialPrestamo)
def sincronizar_historial(sender, instance, **kwargs):
    prestamo = Prestamo.objects.filter(pk=instance.prestamo_id).first()
    if prestamo:
        sincronizar_activo_prestamo(prestamo)


@receiver(pre_save, sender=MovimientoBancario)
def recordar_prestamo_anterior(sender, instance, **kwargs):
    instance._prestamo_anterior_id = (
        sender.objects.filter(pk=instance.pk).values_list('prestamo_id', flat=True).first()
        if instance.pk else None
    )


@receiver(post_save, sender=MovimientoBancario)
@receiver(post_delete, sender=MovimientoBancario)
def sincronizar_devolucion(sender, instance, raw=False, **kwargs):
    if raw:
        return
    ids = {instance.prestamo_id, getattr(instance, '_prestamo_anterior_id', None)} - {None}
    for prestamo in Prestamo.objects.filter(pk__in=ids):
        sincronizar_activo_prestamo(prestamo)


@receiver(post_delete, sender=Prestamo)
def conservar_activo_historico(sender, instance, **kwargs):
    if instance.activo_patrimonial_id:
        Activo.objects.filter(pk=instance.activo_patrimonial_id, user_id=instance.user_id).update(activo=False)
