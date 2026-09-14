"""Mantiene el activo patrimonial de cada cuenta por cobrar actualizado."""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from patrimonio.models import (
    AbonoCuentaPorCobrar,
    CuentaPorCobrar,
    DetalleCuentaPorCobrar,
)


@receiver(post_save, sender=CuentaPorCobrar)
def sincronizar_activo_de_cuenta(sender, instance, **kwargs):
    instance.sincronizar_activo()


@receiver(post_save, sender=DetalleCuentaPorCobrar)
def sincronizar_activo_de_detalle(sender, instance, **kwargs):
    instance.cuenta.sincronizar_activo()


@receiver(post_delete, sender=DetalleCuentaPorCobrar)
def sincronizar_activo_al_borrar_detalle(sender, instance, **kwargs):
    instance.cuenta.sincronizar_activo()


@receiver(post_save, sender=AbonoCuentaPorCobrar)
def sincronizar_activo_de_abono(sender, instance, **kwargs):
    instance.cuenta.sincronizar_activo()


@receiver(post_delete, sender=AbonoCuentaPorCobrar)
def sincronizar_activo_al_borrar_abono(sender, instance, **kwargs):
    instance.cuenta.sincronizar_activo()
