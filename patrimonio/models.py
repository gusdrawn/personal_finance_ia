from django.db import models
from decimal import Decimal


class Activo(models.Model):
    """Assets (cash, investments, loans given, properties, etc)"""
    TIPO_LIQUIDEZ = [
        ('LIQUIDO', 'Líquido'),
        ('NO_LIQUIDO', 'No Líquido'),
    ]
    
    TIPO_ACTIVO = [
        ('EFECTIVO', 'Efectivo'),
        ('INVERSION', 'Inversión'),
        ('PRESTAMO_DADO', 'Préstamo Dado'),
        ('DEPARTAMENTO', 'Departamento'),
        ('AHORRO', 'Ahorro'),
        ('OTRO', 'Otro'),
    ]

    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='activos')
    nombre = models.CharField(max_length=150)
    tipo = models.CharField(max_length=20, choices=TIPO_ACTIVO)
    tipo_liquidez = models.CharField(max_length=20, choices=TIPO_LIQUIDEZ, default='LIQUIDO')
    monto_clp = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    monto_usd = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    notas = models.TextField(blank=True)
    departamento = models.OneToOneField(
        'departamentos.Departamento',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='activo_patrimonial'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Activo'
        verbose_name_plural = 'Activos'
        ordering = ['tipo', 'nombre']

    def __str__(self):
        return f"{self.nombre} - CLP ${self.monto_clp} / USD ${self.monto_usd}"


class Pasivo(models.Model):
    """Liabilities (credit cards, mortgages, loans, etc)"""
    TIPO_PASIVO = [
        ('TDC', 'Tarjeta de Crédito'),
        ('CREDITO_HIPOTECARIO', 'Crédito Hipotecario'),
        ('PRESTAMO', 'Préstamo'),
        ('OTRO', 'Otro'),
    ]

    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='pasivos')
    nombre = models.CharField(max_length=150)
    tipo = models.CharField(max_length=25, choices=TIPO_PASIVO)
    monto_clp = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    monto_usd = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    producto = models.ForeignKey(
        'configuracion.Producto',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='pasivos'
    )
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Pasivo'
        verbose_name_plural = 'Pasivos'
        ordering = ['tipo', 'nombre']

    def __str__(self):
        return f"{self.nombre} - CLP ${self.monto_clp} / USD ${self.monto_usd}"


class SnapshotPatrimonio(models.Model):
    """Monthly snapshots of assets, liabilities, and net worth"""
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='snapshots_patrimonio')
    fecha = models.DateField(db_index=True)
    activos_total_clp = models.DecimalField(max_digits=15, decimal_places=2)
    activos_total_usd = models.DecimalField(max_digits=15, decimal_places=2)
    pasivos_total_clp = models.DecimalField(max_digits=15, decimal_places=2)
    pasivos_total_usd = models.DecimalField(max_digits=15, decimal_places=2)
    patrimonio_neto_clp = models.DecimalField(max_digits=15, decimal_places=2)
    patrimonio_neto_usd = models.DecimalField(max_digits=15, decimal_places=2)
    activos_liquidos_clp = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Snapshot Patrimonio'
        verbose_name_plural = 'Snapshots Patrimonio'
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['user', '-fecha']),
        ]
        unique_together = ('user', 'fecha')

    def __str__(self):
        return f"{self.user.username} - {self.fecha}: Patrimonio ${self.patrimonio_neto_clp}"


class MiniSesion(models.Model):
    """Detailed notes for assets/liabilities with shareable feature"""
    TIPO_MINISESION = [
        ('PRESTAMO_DADO', 'Préstamo Dado'),
        ('COBRO_TERCERO', 'Cobro a Tercero'),
        ('DEUDA', 'Deuda'),
        ('OTRO', 'Otro'),
    ]

    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='minisesiones')
    nombre = models.CharField(max_length=150)
    fecha = models.DateField()
    tipo = models.CharField(max_length=20, choices=TIPO_MINISESION)
    activo_relacionado = models.ForeignKey(
        Activo,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='minisesiones'
    )
    compartible = models.BooleanField(
        default=False,
        help_text="Si es True, se pueden ocultar datos sensibles al compartir"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Mini Sesión'
        verbose_name_plural = 'Mini Sesiones'
        ordering = ['-fecha', 'nombre']

    def __str__(self):
        return f"{self.nombre} ({self.fecha})"

    @property
    def total(self):
        """Sum of all line items"""
        return sum(linea.monto for linea in self.lineas.all())


class LineaMiniSesion(models.Model):
    """Detail lines for mini-sessions"""
    sesion = models.ForeignKey(MiniSesion, on_delete=models.CASCADE, related_name='lineas')
    concepto = models.CharField(max_length=150)
    monto = models.DecimalField(max_digits=15, decimal_places=2)
    orden = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Línea Mini Sesión'
        verbose_name_plural = 'Líneas Mini Sesión'
        ordering = ['sesion', 'orden']

    def __str__(self):
        return f"{self.concepto}: ${self.monto}"
