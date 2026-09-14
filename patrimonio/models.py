from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from decimal import Decimal


class Activo(models.Model):
    """Unified asset model: cash, investments, properties, loans given, etc."""
    HORIZONTE_CHOICES = [
        ('EFECTIVO', 'Efectivo'),
        ('CORTO_PLAZO', 'Corto Plazo'),
        ('MEDIANO_PLAZO', 'Mediano Plazo'),
        ('LARGO_PLAZO', 'Largo Plazo'),
    ]

    TIPO_ACTIVO = [
        ('EFECTIVO', 'Efectivo'),
        ('CRIPTO', 'Criptomoneda'),
        ('FONDO_MUTUO', 'Fondo Mutuo'),
        ('ACCIONES', 'Acciones'),
        ('BROKERAGE', 'Brokerage Account'),
        ('PLAZO_FIJO', 'Plazo Fijo'),
        ('PRESTAMO_DADO', 'Préstamo Dado'),
        ('CUENTA_COBRAR', 'Cuenta por cobrar'),
        ('DEPARTAMENTO', 'Departamento'),
        ('AHORRO', 'Ahorro'),
        ('OTRO', 'Otro'),
    ]

    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='activos')
    nombre = models.CharField(max_length=150)
    tipo = models.CharField(max_length=20, choices=TIPO_ACTIVO)
    horizonte_temporal = models.CharField(
        max_length=20, choices=HORIZONTE_CHOICES, default='EFECTIVO',
        help_text="Horizonte de inversión del activo"
    )
    es_liquido = models.BooleanField(default=True, help_text="¿Se puede liquidar fácilmente?")
    monto_clp = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    monto_usd = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    activo = models.BooleanField(default=True)
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
        ordering = ['horizonte_temporal', 'tipo', 'nombre']

    def __str__(self):
        return f"{self.nombre} - CLP ${self.monto_clp} / USD ${self.monto_usd}"


class CuentaPorCobrar(models.Model):
    """Agrupador de importes pagados por el usuario que aún debe recuperar."""

    MONEDA_CHOICES = [
        ("CLP", "Peso chileno"),
        ("USD", "Dólar"),
    ]

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="cuentas_por_cobrar"
    )
    nombre = models.CharField(max_length=150)
    descripcion = models.TextField(blank=True)
    moneda = models.CharField(max_length=3, choices=MONEDA_CHOICES, default="CLP")
    activo = models.BooleanField(default=True)
    activo_patrimonial = models.OneToOneField(
        Activo,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cuenta_por_cobrar",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cuenta por cobrar"
        verbose_name_plural = "Cuentas por cobrar"
        ordering = ["-activo", "nombre"]

    def __str__(self):
        return self.nombre

    @property
    def total_cargado(self):
        return sum((detalle.monto for detalle in self.detalles.all()), Decimal("0"))

    @property
    def total_abonado(self):
        return sum((abono.monto for abono in self.abonos.all()), Decimal("0"))

    @property
    def saldo_pendiente(self):
        return max(self.total_cargado - self.total_abonado, Decimal("0"))

    def sincronizar_activo(self):
        """Refleja el saldo pendiente en el activo unificado del patrimonio."""
        saldo = self.saldo_pendiente
        activo = (
            Activo.objects.filter(pk=self.activo_patrimonial_id).first()
            if self.activo_patrimonial_id
            else None
        )
        if activo is not None and activo.user_id != self.user_id:
            activo = None
        if activo is None:
            activo = Activo.objects.create(
                user=self.user,
                nombre=self.nombre,
                tipo="CUENTA_COBRAR",
                horizonte_temporal="CORTO_PLAZO",
                es_liquido=False,
                activo=self.activo,
            )
            type(self).objects.filter(pk=self.pk).update(activo_patrimonial=activo)
            self.activo_patrimonial_id = activo.pk

        activo.nombre = self.nombre
        activo.tipo = "CUENTA_COBRAR"
        activo.horizonte_temporal = "CORTO_PLAZO"
        activo.es_liquido = False
        activo.activo = self.activo
        activo.monto_clp = saldo if self.moneda == "CLP" else Decimal("0")
        activo.monto_usd = saldo if self.moneda == "USD" else Decimal("0")
        activo.notas = self.descripcion
        activo.save(
            update_fields=[
                "nombre",
                "tipo",
                "horizonte_temporal",
                "es_liquido",
                "activo",
                "monto_clp",
                "monto_usd",
                "notas",
                "updated_at",
            ]
        )


class DetalleCuentaPorCobrar(models.Model):
    """Una obligación o cargo que compone una cuenta por cobrar."""

    ORIGEN_CHOICES = [
        ("MANUAL", "Manual"),
        ("MOVIMIENTO", "Tarjeta de crédito"),
        ("NEAT", "NeatPagos"),
    ]

    cuenta = models.ForeignKey(
        CuentaPorCobrar, on_delete=models.CASCADE, related_name="detalles"
    )
    descripcion = models.CharField(max_length=255)
    periodo = models.DateField(db_index=True, help_text="Mes al que corresponde el cobro")
    fecha = models.DateField(help_text="Fecha del cargo o registro manual")
    monto = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    origen = models.CharField(max_length=12, choices=ORIGEN_CHOICES, default="MANUAL")
    monto_manual = models.BooleanField(
        default=True,
        help_text="Permite distinguir una cifra ingresada a mano de una detectada.",
    )
    provisional = models.BooleanField(
        default=False,
        help_text="El cargo ya ocurrió, pero su fuente bancaria aún no fue importada.",
    )
    movimiento = models.ForeignKey(
        "banca.MovimientoBancario",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="detalles_cuentas_por_cobrar",
    )
    evento_externo = models.ForeignKey(
        "banca.EventoFinancieroExterno",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="detalles_cuentas_por_cobrar",
    )
    clave_similitud = models.CharField(max_length=255, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Detalle de cuenta por cobrar"
        verbose_name_plural = "Detalles de cuentas por cobrar"
        ordering = ["-periodo", "-fecha", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=~(
                    models.Q(movimiento__isnull=False)
                    & models.Q(evento_externo__isnull=False)
                ),
                name="patrimonio_cobro_unica_fuente",
            ),
            models.UniqueConstraint(
                fields=["cuenta", "movimiento"],
                condition=models.Q(movimiento__isnull=False),
                name="patrimonio_cobro_movimiento_unico",
            ),
            models.UniqueConstraint(
                fields=["cuenta", "evento_externo"],
                condition=models.Q(evento_externo__isnull=False),
                name="patrimonio_cobro_evento_unico",
            ),
        ]

    def clean(self):
        if self.movimiento_id and self.movimiento.user_id != self.cuenta.user_id:
            raise ValidationError("El movimiento debe pertenecer al mismo usuario de la cuenta.")
        if self.evento_externo_id and self.evento_externo.user_id != self.cuenta.user_id:
            raise ValidationError("El evento NeatPagos debe pertenecer al mismo usuario de la cuenta.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.cuenta.nombre} · {self.descripcion} · {self.monto}"

    @property
    def total_abonado(self):
        return sum((abono.monto for abono in self.abonos.all()), Decimal("0"))

    @property
    def saldo_pendiente(self):
        return max(self.monto - self.total_abonado, Decimal("0"))


class AbonoCuentaPorCobrar(models.Model):
    """Pago recibido, asociado opcionalmente a una línea específica."""

    cuenta = models.ForeignKey(
        CuentaPorCobrar, on_delete=models.CASCADE, related_name="abonos"
    )
    detalle = models.ForeignKey(
        DetalleCuentaPorCobrar,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="abonos",
    )
    fecha = models.DateField(db_index=True)
    monto = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    descripcion = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Abono de cuenta por cobrar"
        verbose_name_plural = "Abonos de cuentas por cobrar"
        ordering = ["-fecha", "-id"]

    def clean(self):
        if self.detalle_id and self.detalle.cuenta_id != self.cuenta_id:
            raise ValidationError("El abono debe apuntar a una línea de su misma cuenta.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.cuenta.nombre} · abono {self.monto}"


class HistorialActivo(models.Model):
    """Historical snapshots of asset values"""
    activo = models.ForeignKey(Activo, on_delete=models.CASCADE, related_name='historial')
    fecha = models.DateField(db_index=True)
    monto_clp = models.DecimalField(max_digits=15, decimal_places=2)
    monto_usd = models.DecimalField(max_digits=15, decimal_places=2)
    valor_uf = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    valor_dolar = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Historial Activo'
        verbose_name_plural = 'Historiales Activos'
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['activo', '-fecha']),
        ]
        unique_together = ('activo', 'fecha')

    def __str__(self):
        return f"{self.activo.nombre} - {self.fecha}"


class Pasivo(models.Model):
    """Liabilities (credit cards, mortgages, loans, etc)"""
    TIPO_PASIVO = [
        ('TDC', 'Tarjeta de Crédito'),
        ('CREDITO_HIPOTECARIO', 'Crédito Hipotecario'),
        ('CREDITO_CONSUMO', 'Crédito de Consumo'),
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
    activo = models.BooleanField(default=True, help_text="Si la deuda sigue vigente")
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
