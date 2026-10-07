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
    """Keep the latest bank balance as the source for generated liabilities."""
    try:
        from patrimonio.models import Pasivo
    except Exception:
        return

    producto = instance.producto
    latest = (
        SaldoSnapshot.objects.filter(user=instance.user, producto=producto)
        .order_by("-fecha", "-id")
        .first()
    )
    if not latest or latest.pk != instance.pk:
        return

    tipo_cambio = None
    try:
        from configuracion.models import TipoCambio

        tipo_cambio = (
            TipoCambio.objects.filter(fuente="mindicador.cl", fecha__lte=instance.fecha)
            .order_by("-fecha", "-id")
            .first()
        )
    except Exception:
        tipo_cambio = None

    base_nombre = f"{producto.banco.nombre} - {producto.nombre}"
    tipo_pasivo = producto.tipo if producto.tipo in dict(Pasivo.TIPO_PASIVO) else "OTRO"
    tiene_clp = any(value is not None for value in (
        instance.deuda_facturada_clp, instance.deuda_no_facturada_clp, instance.saldo_insoluto,
    ))
    importes = {
        "CLP": instance.deuda_total_clp if tiene_clp else None,
        "USD": instance.deuda_usd,
    }
    tiene_dos_monedas = all(valor is not None and valor != 0 for valor in importes.values())
    for moneda, importe in importes.items():
        # A partial snapshot must not replace a known balance with an invented zero.
        if importe is None:
            continue
        pasivo, _ = Pasivo.objects.get_or_create(
            producto=producto,
            moneda_original=moneda,
            sincronizado_banca=True,
            defaults={
                "user": instance.user,
                "nombre": f"{base_nombre} ({moneda})" if tiene_dos_monedas else base_nombre,
                "tipo": tipo_pasivo,
                "activo": importe != 0,
                "notas": "Auto-sincronizado desde banca (SaldoSnapshot)",
            },
        )
        pasivo.nombre = f"{base_nombre} ({moneda})" if tiene_dos_monedas else base_nombre
        pasivo.tipo = tipo_pasivo
        pasivo.user = instance.user
        pasivo.sincronizado_banca = True
        pasivo.activo = importe != 0
        pasivo.requiere_revision_moneda = False
        pasivo.monto_original = importe
        pasivo.moneda_original = moneda
        if moneda == "CLP":
            pasivo.monto_clp = importe
            if tipo_cambio and tipo_cambio.dolar:
                pasivo.monto_usd = (importe / tipo_cambio.dolar).quantize(Decimal("0.01"))
        else:
            pasivo.monto_usd = importe
            tasa = instance.tipo_cambio_banco or getattr(tipo_cambio, "dolar", None)
            pasivo.monto_clp = (importe * tasa).quantize(Decimal("0.01")) if tasa else Decimal("0")
        pasivo.save(update_fields=[
            "nombre", "tipo", "user", "sincronizado_banca", "activo",
            "requiere_revision_moneda", "monto_original", "moneda_original",
            "monto_clp", "monto_usd", "updated_at",
        ])

    if producto.tipo == "CREDITO_HIPOTECARIO":
        try:
            from departamentos.models import CreditoHipotecario

            hipotecas = CreditoHipotecario.objects.filter(producto=producto, departamento__user=instance.user, activo=True)
            saldo_uf = instance.saldo_insoluto_uf
            if saldo_uf is None and instance.saldo_insoluto and tipo_cambio and tipo_cambio.uf:
                saldo_uf = instance.saldo_insoluto / tipo_cambio.uf
            if saldo_uf is not None:
                hipotecas.update(saldo_actual_uf=saldo_uf)
        except Exception:
            # La creación de un pasivo no debe depender de que exista una hipoteca
            # inmobiliaria asociada en esta instalación.
            pass
