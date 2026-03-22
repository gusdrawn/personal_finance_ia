from django.db import models


class Inversion(models.Model):
    """Investment accounts and assets"""
    TIPO_INVERSION = [
        ('CRIPTO', 'Criptomoneda'),
        ('FONDO_MUTUO', 'Fondo Mutuo'),
        ('ACCIONES', 'Acciones'),
        ('BROKERAGE', 'Brokerage Account'),
        ('PLAZO_FIJO', 'Plazo Fijo'),
        ('OTRO', 'Otro'),
    ]

    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='inversiones')
    nombre = models.CharField(max_length=150)
    tipo = models.CharField(max_length=20, choices=TIPO_INVERSION)
    monto_clp = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    monto_usd = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    porcentaje_cartera = models.DecimalField(max_digits=5, decimal_places=2, default=0, help_text="% of total portfolio")
    activo = models.BooleanField(default=True)
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Inversión'
        verbose_name_plural = 'Inversiones'
        ordering = ['tipo', 'nombre']

    def __str__(self):
        return f"{self.nombre} - CLP ${self.monto_clp} / USD ${self.monto_usd}"


class HistorialInversion(models.Model):
    """Historical snapshots of investment values"""
    inversion = models.ForeignKey(Inversion, on_delete=models.CASCADE, related_name='historial')
    fecha = models.DateField(db_index=True)
    monto_clp = models.DecimalField(max_digits=15, decimal_places=2)
    monto_usd = models.DecimalField(max_digits=15, decimal_places=2)
    valor_uf = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    valor_dolar = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Historial Inversión'
        verbose_name_plural = 'Historiales Inversiones'
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['inversion', '-fecha']),
        ]
        unique_together = ('inversion', 'fecha')

    def __str__(self):
        return f"{self.inversion.nombre} - {self.fecha}"
