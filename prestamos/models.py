from decimal import Decimal
from datetime import date

from django.db import models, transaction
from django.core.exceptions import ValidationError


class Prestamo(models.Model):
    TIPO_PRESTAMO = [
        ('PERSONAL', 'Bicicleta Personal (Deuda Propia)'),
        ('TERCEROS', 'Bicicleta de Terceros (Préstamo a otro)'),
    ]

    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='prestamos')
    nombre = models.CharField(max_length=150)
    tipo = models.CharField(max_length=20, choices=TIPO_PRESTAMO)
    monto_total = models.DecimalField(max_digits=15, decimal_places=2)
    costo_mensual_porcentaje = models.DecimalField(
        max_digits=5, 
        decimal_places=2, 
        default=0, 
        help_text="Costo mensual o interés (%)"
    )
    tarjeta_asociada = models.ForeignKey(
        'configuracion.Producto', 
        null=True, 
        blank=True, 
        on_delete=models.SET_NULL, 
        related_name='prestamos_asociados',
        help_text="Tarjeta de la que se sacó el préstamo (opcional)"
    )
    notas = models.TextField(blank=True)
    activo_patrimonial = models.OneToOneField(
        'patrimonio.Activo', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='prestamo_recuperable', editable=False,
    )
    activo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Préstamo (Bicicleta)'
        verbose_name_plural = 'Préstamos (Bicicletas)'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.nombre} - {self.get_tipo_display()} (${self.monto_total})"

    def clean(self):
        if self.activo_patrimonial_id and self.activo_patrimonial.user_id != self.user_id:
            raise ValidationError({'activo_patrimonial': 'El activo debe pertenecer al usuario.'})
        if self.tarjeta_asociada_id and self.tarjeta_asociada.user_id != self.user_id:
            raise ValidationError({'tarjeta_asociada': 'El producto debe pertenecer al usuario.'})

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def monto_mensual(self):
        """Interés/costo mensual vigente para mostrarlo junto a la bicicleta."""
        if not self.activo:
            return Decimal("0")
        return self.monto_total * self.costo_mensual_porcentaje / Decimal("100")

    def estado_en(self, fecha: date):
        """Último valor conocido en la fecha; None si aún no hay registro."""
        return self.historial.filter(fecha__lte=fecha).order_by("-fecha", "-id").first()

    def saldo_recuperable(self, fecha: date | None = None):
        """Capital vigente menos devoluciones CLP confirmadas hasta el corte."""
        from django.db.models import Sum
        from django.utils import timezone

        fecha = fecha or timezone.localdate()
        estado = self.estado_en(fecha)
        if estado is None and fecha < timezone.localdate(self.created_at):
            return Decimal('0')
        capital = estado.monto_total if estado else self.monto_total
        movimientos = self.movimientos_banca.filter(
            user_id=self.user_id, producto__user_id=self.user_id,
            moneda='CLP', monto__lt=0, fecha__lte=fecha, por_revisar=False,
        ).exclude(estado='DESCARTADO_CIERRE')
        if estado and estado.es_saldo_confirmado:
            # A confirmed closing balance already includes earlier repayments,
            # including those imported after the balance was entered.
            movimientos = movimientos.filter(fecha__gt=estado.fecha)
        devuelto = movimientos.aggregate(total=Sum('monto'))['total'] or Decimal('0')
        return max(Decimal(capital) + devuelto, Decimal('0'))


class HistorialPrestamo(models.Model):
    prestamo = models.ForeignKey(Prestamo, on_delete=models.CASCADE, related_name="historial")
    fecha = models.DateField(db_index=True)
    monto_total = models.DecimalField(max_digits=15, decimal_places=2)
    activo = models.BooleanField(default=True)
    es_saldo_confirmado = models.BooleanField(
        default=False, help_text='Saldo al cierre de la fecha; incluye abonos anteriores.',
    )
    motivo = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-id"]
        indexes = [models.Index(fields=["prestamo", "fecha"], name="prestamos_h_prestam_95dacc_idx")]
