from django.db import models


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
    activo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Préstamo (Bicicleta)'
        verbose_name_plural = 'Préstamos (Bicicletas)'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.nombre} - {self.get_tipo_display()} (${self.monto_total})"
