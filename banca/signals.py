"""
Señales de banca.

Cuando se crea/actualiza un SaldoSnapshot, se refleja la deuda en el Pasivo del producto
(integración con patrimonio / dashboard, §16.4). Así los pasivos del patrimonio dejan de
cargarse a mano.
"""

from __future__ import annotations

from decimal import Decimal

from django.db.models.signals import post_save
from django.dispatch import receiver

from banca.models import SaldoSnapshot


@receiver(post_save, sender=SaldoSnapshot)
def sync_pasivo_desde_snapshot(sender, instance: SaldoSnapshot, **kwargs):
    """Actualiza (o crea) el Pasivo del producto con la deuda total del snapshot."""
    try:
        from patrimonio.models import Pasivo
    except Exception:
        return

    producto = instance.producto
    deuda_clp = instance.deuda_total_clp or Decimal("0")
    deuda_usd = instance.deuda_usd or Decimal("0")

    pasivo, _ = Pasivo.objects.get_or_create(
        producto=producto,
        defaults={
            "user": instance.user,
            "nombre": f"{producto.banco.nombre} - {producto.nombre}",
            "tipo": producto.tipo if producto.tipo in dict(Pasivo.TIPO_PASIVO) else "OTRO",
            "monto_clp": deuda_clp,
            "monto_usd": deuda_usd,
            "activo": True,
            "notas": "Auto-sincronizado desde banca (SaldoSnapshot)",
        },
    )
    # actualizar montos en cada snapshot nuevo
    pasivo.monto_clp = deuda_clp
    pasivo.monto_usd = deuda_usd
    pasivo.activo = True
    pasivo.save(update_fields=["monto_clp", "monto_usd", "activo", "updated_at"])
