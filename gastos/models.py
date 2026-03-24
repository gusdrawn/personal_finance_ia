from django.db import models
from decimal import Decimal

MONEDA_CHOICES = [
    ('CLP', 'Chilean Peso'),
    ('USD', 'US Dollar'),
    ('UF', 'Unidad de Fomento'),
]


class CategoriaIngreso(models.Model):
    """Income and expense categories (dynamic and editable)"""
    TIPO_CATEGORIA = [
        ('INGRESO', 'Ingreso'),
        ('GASTO_FIJO', 'Gasto Fijo'),
        ('SUSCRIPCION', 'Suscripción'),
        ('SEGURO', 'Seguro'),
        ('TDC', 'Tarjeta de Crédito'),
        ('COBRO_BANCO', 'Cobro de Banco'),
    ]

    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='categorias_ingresos')
    nombre = models.CharField(max_length=150)
    tipo = models.CharField(max_length=20, choices=TIPO_CATEGORIA)
    orden = models.IntegerField(default=0)
    activo = models.BooleanField(default=True)
    banco_defecto = models.ForeignKey(
        'configuracion.Banco',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='categorias_ingresos'
    )
    contabilizar = models.BooleanField(
        default=True, 
        help_text="Si es Falso, no suma al gasto total (ej. ya pagado en TDC)"
    )
    moneda_defecto = models.CharField(
        max_length=3, 
        choices=MONEDA_CHOICES, 
        default='CLP'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Categoría de Ingreso'
        verbose_name_plural = 'Categorías de Ingreso'
        ordering = ['tipo', 'orden', 'nombre']
        unique_together = ('user', 'nombre', 'tipo')

    def __str__(self):
        return f"{self.get_tipo_display()}: {self.nombre}"


class RegistroMensual(models.Model):
    """Monthly income/expense records"""
    TIPO_REGISTRO = [
        ('INGRESO', 'Ingreso'),
        ('GASTO', 'Gasto'),
        ('SUSCRIPCION', 'Suscripción'),
        ('SEGURO', 'Seguro'),
        ('TDC', 'Tarjeta de Crédito'),
        ('COBRO_BANCO', 'Cobro de Banco'),
    ]

    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='registros_mensuales')
    year = models.IntegerField()
    mes = models.IntegerField(choices=[(i, f'Month {i}') for i in range(1, 13)])
    categoria = models.ForeignKey(CategoriaIngreso, on_delete=models.PROTECT, related_name='registros')
    monto = models.DecimalField(max_digits=15, decimal_places=2)
    monto_contable_clp = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
        help_text="Valor fijo convertido a CLP al momento de crearse si moneda es USD/UF"
    )
    moneda = models.CharField(max_length=3, choices=MONEDA_CHOICES, default='CLP')
    tipo = models.CharField(max_length=20, choices=TIPO_REGISTRO)
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Registro Mensual'
        verbose_name_plural = 'Registros Mensuales'
        ordering = ['year', 'mes', 'categoria']
        indexes = [
            models.Index(fields=['user', 'year', 'mes']),
            models.Index(fields=['categoria', 'year', 'mes']),
        ]
        unique_together = ('user', 'year', 'mes', 'categoria')

    def __str__(self):
        return f"{self.categoria} - {self.year}-{self.mes:02d}: {self.monto} {self.moneda}"


class GastoAnual(models.Model):
    """Annual expenses to save for (Pro rata savings calculation)"""
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='gastos_anuales')
    nombre = models.CharField(max_length=150)
    monto = models.DecimalField(max_digits=15, decimal_places=2)
    mes_cobro = models.IntegerField(choices=[(i, f'Month {i}') for i in range(1, 13)])
    activo = models.BooleanField(default=True)
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Gasto Anual'
        verbose_name_plural = 'Gastos Anuales'
        ordering = ['mes_cobro', 'nombre']

    def __str__(self):
        return f"{self.nombre} (${self.monto}) - Mes {self.mes_cobro}"

    @property
    def ahorro_mensual(self):
        """Monthly savings required to cover this annual expense"""
        return self.monto / 12


class GastoTrimestral(models.Model):
    """Quarterly expenses to save for (Pro rata savings calculation)"""
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='gastos_trimestrales')
    nombre = models.CharField(max_length=150)
    monto = models.DecimalField(max_digits=15, decimal_places=2)
    trimestre = models.IntegerField(choices=[(i, f'Quarter {i}') for i in range(1, 5)])
    activo = models.BooleanField(default=True)
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Gasto Trimestral'
        verbose_name_plural = 'Gastos Trimestrales'
        ordering = ['trimestre', 'nombre']

    def __str__(self):
        return f"{self.nombre} (${self.monto}) - Q{self.trimestre}"

    @property
    def ahorro_mensual(self):
        """Monthly savings required to cover this quarterly expense"""
        return self.monto / 3
