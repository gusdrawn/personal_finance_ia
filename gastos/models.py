from django.db import models
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from decimal import Decimal

MONEDA_CHOICES = [
    ('CLP', 'Chilean Peso'),
    ('USD', 'US Dollar'),
    ('UF', 'Unidad de Fomento'),
]


class MovimientoManual(models.Model):
    """Movimiento de caja que todavía no proviene de una fuente importada."""

    DIRECCIONES = [("INGRESO", "Ingreso"), ("EGRESO", "Egreso")]
    NATURALEZAS = [
        ("OPERACIONAL", "Operacional"),
        ("ACTIVO_RECUPERABLE", "Conversión a activo recuperable"),
        ("INVERSION", "Inversión"),
        ("TRANSFERENCIA_INTERNA", "Transferencia interna"),
    ]

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="movimientos_manuales"
    )
    fecha = models.DateField(db_index=True)
    periodo = models.DateField(db_index=True, help_text="Primer día del mes contable")
    direccion = models.CharField(max_length=8, choices=DIRECCIONES)
    monto = models.DecimalField(
        max_digits=15, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    moneda = models.CharField(max_length=3, choices=MONEDA_CHOICES, default="CLP")
    monto_clp = models.DecimalField(max_digits=15, decimal_places=2)
    tipo_cambio = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True
    )
    monto_clp_estimado = models.BooleanField(default=False)
    categoria = models.ForeignKey(
        "banca.CategoriaTematica",
        on_delete=models.PROTECT,
        related_name="movimientos_manuales",
    )
    departamento = models.ForeignKey(
        "departamentos.Departamento",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="movimientos_manuales",
    )
    naturaleza = models.CharField(
        max_length=24, choices=NATURALEZAS, default="OPERACIONAL"
    )
    descripcion = models.CharField(max_length=255)
    notas = models.TextField(blank=True)
    es_resumen_mensual = models.BooleanField(default=False)
    modo_resumen = models.CharField(
        max_length=10, choices=[("TOTAL", "Total del mes"), ("ADICIONAL", "Importe adicional")],
        default="TOTAL", help_text="TOTAL incluye lo ya registrado en la categoría; ADICIONAL se suma.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Movimiento manual"
        verbose_name_plural = "Movimientos manuales"
        ordering = ["-fecha", "-id"]
        indexes = [
            models.Index(fields=["user", "periodo", "direccion"]),
            models.Index(fields=["user", "categoria", "periodo"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "periodo", "categoria", "direccion"],
                condition=models.Q(es_resumen_mensual=True),
                name="gastos_resumen_manual_categoria_mes_un",
            )
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.periodo.day != 1:
            raise ValidationError({"periodo": "El período debe usar el primer día del mes."})
        if self.categoria_id and self.categoria.user_id != self.user_id:
            raise ValidationError({"categoria": "La categoría debe pertenecer al usuario."})
        if self.departamento_id and self.departamento.user_id != self.user_id:
            raise ValidationError({"departamento": "La propiedad debe pertenecer al usuario."})

    @property
    def contabiliza_caja(self):
        return self.naturaleza != "TRANSFERENCIA_INTERNA"

    @property
    def contabiliza_consumo(self):
        return self.direccion == "EGRESO" and self.naturaleza == "OPERACIONAL"

    def __str__(self):
        return f"{self.fecha} · {self.descripcion} · {self.monto} {self.moneda}"


class ConciliacionMovimientoManual(models.Model):
    """Link or review suggestion between a manual entry and its bank source."""

    ESTADOS = [
        ("SUGERIDA", "Sugerida"),
        ("CONFIRMADA", "Confirmada"),
        ("DESCARTADA", "Descartada"),
        ("OBSOLETA", "Sin vigencia"),
    ]
    movimiento_manual = models.ForeignKey(
        MovimientoManual, on_delete=models.CASCADE, related_name="conciliaciones_bancarias"
    )
    movimiento_bancario = models.ForeignKey(
        "banca.MovimientoBancario",
        on_delete=models.CASCADE,
        related_name="conciliaciones_manuales",
    )
    estado = models.CharField(max_length=12, choices=ESTADOS, default="SUGERIDA")
    confirmada_manual = models.BooleanField(default=False)
    creada_at = models.DateTimeField(auto_now_add=True)
    actualizada_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-actualizada_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["movimiento_manual", "movimiento_bancario"],
                name="gastos_conciliacion_manual_banco_unica",
            ),
            models.UniqueConstraint(
                fields=["movimiento_manual"],
                condition=models.Q(estado="CONFIRMADA"),
                name="gastos_conciliacion_manual_confirmada_unica",
            ),
            models.UniqueConstraint(
                fields=["movimiento_bancario"],
                condition=models.Q(estado="CONFIRMADA"),
                name="gastos_conciliacion_banco_confirmada_unica",
            ),
        ]

    def clean(self):
        if self.movimiento_manual_id and self.movimiento_bancario_id:
            if self.movimiento_manual.user_id != self.movimiento_bancario.user_id:
                raise ValidationError("Los movimientos deben pertenecer al mismo usuario.")
            if self.movimiento_manual.es_resumen_mensual:
                raise ValidationError("Un resumen mensual no se puede conciliar con un movimiento bancario.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


FRECUENCIA_CHOICES = [
    ('MENSUAL', 'Mensual'),
    ('BIMESTRAL', 'Bimestral'),
    ('TRIMESTRAL', 'Trimestral'),
    ('SEMESTRAL', 'Semestral'),
    ('ANUAL', 'Anual'),
]

class GastoProgramado(models.Model):
    """Gastos planificados con frecuencia específica (Agenda)"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='gastos_programados')
    nombre = models.CharField(max_length=150)
    monto = models.DecimalField(max_digits=15, decimal_places=2)
    fecha_inicio = models.DateField(help_text="Fecha base para calcular próximos cobros")
    frecuencia = models.CharField(max_length=20, choices=FRECUENCIA_CHOICES, default='MENSUAL')
    activo = models.BooleanField(default=True)
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Gasto Programado'
        verbose_name_plural = 'Gastos Programados'
        ordering = ['fecha_inicio', 'nombre']

    def __str__(self):
        return f"{self.nombre} ({self.get_frecuencia_display()})"

    @property
    def ahorro_mensual(self):
        """Ahorro mensual necesario para cubrir este gasto"""
        if self.frecuencia == 'MENSUAL': return self.monto
        elif self.frecuencia == 'BIMESTRAL': return self.monto / Decimal('2.0')
        elif self.frecuencia == 'TRIMESTRAL': return self.monto / Decimal('3.0')
        elif self.frecuencia == 'SEMESTRAL': return self.monto / Decimal('6.0')
        elif self.frecuencia == 'ANUAL': return self.monto / Decimal('12.0')
        return self.monto
