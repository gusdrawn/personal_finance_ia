from __future__ import annotations

from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum


TIPO_CHOICES = [
    ("PAGO", "Pago"),
    ("DOCUMENTO", "Documento"),
    ("SINCRONIZACION", "Sincronización"),
    ("MANUAL", "Manual"),
]
ESTADO_CHOICES = [
    ("PENDIENTE", "Pendiente"),
    ("COMPLETADA", "Completada"),
    ("NO_APLICA", "No aplica"),
    ("SIN_CARTOLA", "Sin cartola"),
    ("SIN_MOVIMIENTOS", "Sin movimientos"),
    ("INCLUIDA_EN_OTRO_PAGO", "Incluida en otro pago"),
]
PERIODICIDAD_CHOICES = [
    ("UNICA", "Una vez"),
    ("MENSUAL", "Mensual"),
    ("BIMESTRAL", "Bimestral"),
    ("TRIMESTRAL", "Trimestral"),
    ("SEMESTRAL", "Semestral"),
    ("ANUAL", "Anual"),
    ("MESES", "Meses configurables"),
    ("DESDE_ULTIMO_REGISTRO", "Desde el último registro"),
]
CANAL_COBRO_CHOICES = [
    ("DIRECTO", "Cobro directo"),
    ("INCLUIDO_CONTRIBUCIONES", "Incluido en contribuciones"),
    ("DESCONOCIDO", "Por confirmar"),
]


class ReglaPendiente(models.Model):
    """Definition of a recurring obligation or maintenance task."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="reglas_pendientes")
    nombre = models.CharField(max_length=180)
    descripcion = models.TextField(blank=True)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default="MANUAL")
    clave_fuente = models.CharField(
        max_length=180,
        blank=True,
        default="",
        help_text="Clave idempotente para reglas automáticas.",
    )
    departamento = models.ForeignKey(
        "departamentos.Departamento",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="reglas_pendientes",
    )
    gasto_programado = models.OneToOneField(
        "gastos.GastoProgramado",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="regla_pendiente",
        help_text="Proyección de presupuesto vinculada a esta regla de pago.",
    )
    producto = models.ForeignKey(
        "configuracion.Producto",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="reglas_pendientes",
    )
    periodicidad = models.CharField(max_length=28, choices=PERIODICIDAD_CHOICES, default="UNICA")
    fecha_inicio = models.DateField(null=True, blank=True)
    dia_vencimiento = models.PositiveSmallIntegerField(null=True, blank=True)
    meses_vencimiento = models.JSONField(default=list, blank=True)
    intervalo_dias = models.PositiveSmallIntegerField(default=30)
    monto_estimado = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    moneda = models.CharField(max_length=3, default="CLP")
    url_pago = models.URLField(blank=True)
    canal_cobro = models.CharField(max_length=32, choices=CANAL_COBRO_CHOICES, default="DESCONOCIDO")
    recordar_dias = models.PositiveSmallIntegerField(default=3)
    umbral_dias_inactividad = models.PositiveSmallIntegerField(default=30)
    contabiliza_gasto = models.BooleanField(default=True)
    automatica = models.BooleanField(default=False)
    activa = models.BooleanField(default=True)
    creada_at = models.DateTimeField(auto_now_add=True)
    actualizada_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nombre", "fecha_inicio"]
        indexes = [
            models.Index(fields=["user", "activa", "tipo"], name="pend_regla_user_activa_tipo"),
            models.Index(fields=["user", "departamento", "activa"], name="pend_regla_user_depto_activa"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "clave_fuente"],
                condition=~models.Q(clave_fuente=""),
                name="pendiente_regla_user_clave_unica",
            )
        ]

    def clean(self):
        if self.departamento_id and self.departamento.user_id != self.user_id:
            raise ValidationError({"departamento": "La propiedad debe pertenecer al usuario."})
        if self.producto_id and self.producto.user_id != self.user_id:
            raise ValidationError({"producto": "El producto debe pertenecer al usuario."})
        if self.gasto_programado_id and self.gasto_programado.user_id != self.user_id:
            raise ValidationError({"gasto_programado": "El gasto programado debe pertenecer al usuario."})
        if self.tipo == "PAGO" and not self.departamento_id and not self.producto_id:
            # Generic services are valid, but property payment rules should be explicit.
            return
        if self.dia_vencimiento and not 1 <= self.dia_vencimiento <= 31:
            raise ValidationError({"dia_vencimiento": "El día debe estar entre 1 y 31."})

    def __str__(self):
        return self.nombre


class OcurrenciaPendiente(models.Model):
    """Concrete due date and its resolution, generated idempotently from a rule."""

    regla = models.ForeignKey(ReglaPendiente, on_delete=models.CASCADE, related_name="ocurrencias")
    importacion = models.ForeignKey(
        "banca.ImportacionEstadoCuenta",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ocurrencias_pendientes",
    )
    fecha_vencimiento = models.DateField(db_index=True)
    monto_esperado = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    estado = models.CharField(max_length=28, choices=ESTADO_CHOICES, default="PENDIENTE", db_index=True)
    fecha_real = models.DateField(null=True, blank=True)
    monto_real = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    nota_resolucion = models.TextField(blank=True)
    completada_at = models.DateTimeField(null=True, blank=True)
    creada_at = models.DateTimeField(auto_now_add=True)
    actualizada_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["estado", "fecha_vencimiento", "regla__nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["regla", "fecha_vencimiento"],
                name="pendiente_ocurrencia_regla_fecha_unica",
            )
        ]
        indexes = [
            models.Index(fields=["regla", "estado", "fecha_vencimiento"], name="pend_ocur_regla_estado_fecha"),
            models.Index(fields=["fecha_vencimiento", "estado"], name="pend_ocur_fecha_estado"),
        ]

    @property
    def esta_resuelta(self):
        return self.estado != "PENDIENTE"

    def __str__(self):
        return f"{self.regla.nombre} · {self.fecha_vencimiento:%d/%m/%Y}"


class AsignacionPagoPendiente(models.Model):
    """Allocates a payment to one occurrence without duplicating the source movement.

    A bank movement may be allocated to several properties, which is needed for
    municipal receipts that group several rolls in one Webpay payment.
    """

    ocurrencia = models.ForeignKey(OcurrenciaPendiente, on_delete=models.CASCADE, related_name="asignaciones_pago")
    movimiento_bancario = models.ForeignKey(
        "banca.MovimientoBancario",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="asignaciones_pendientes",
    )
    movimiento_manual = models.ForeignKey(
        "gastos.MovimientoManual",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="asignaciones_pendientes",
    )
    importacion = models.ForeignKey(
        "banca.ImportacionEstadoCuenta",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="asignaciones_pendientes",
    )
    monto_asignado = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    referencia_pago = models.CharField(max_length=120, blank=True)
    creada_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creada_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    (models.Q(movimiento_bancario__isnull=False) & models.Q(movimiento_manual__isnull=True) & models.Q(importacion__isnull=True))
                    | (models.Q(movimiento_bancario__isnull=True) & models.Q(movimiento_manual__isnull=False) & models.Q(importacion__isnull=True))
                    | (models.Q(movimiento_bancario__isnull=True) & models.Q(movimiento_manual__isnull=True) & models.Q(importacion__isnull=False))
                ),
                name="pendiente_asignacion_un_solo_movimiento",
            ),
            models.UniqueConstraint(
                fields=["ocurrencia", "movimiento_bancario"],
                condition=models.Q(movimiento_bancario__isnull=False),
                name="pendiente_asignacion_banco_unica",
            ),
            models.UniqueConstraint(
                fields=["ocurrencia", "movimiento_manual"],
                condition=models.Q(movimiento_manual__isnull=False),
                name="pendiente_asignacion_manual_unica",
            ),
            models.UniqueConstraint(
                fields=["ocurrencia", "importacion"],
                condition=models.Q(importacion__isnull=False),
                name="pendiente_asignacion_importacion_unica",
            ),
        ]

    def clean(self):
        user_id = self.ocurrencia.regla.user_id
        if self.movimiento_bancario_id and self.movimiento_bancario.user_id != user_id:
            raise ValidationError("El movimiento bancario debe pertenecer al usuario.")
        if self.movimiento_manual_id and self.movimiento_manual.user_id != user_id:
            raise ValidationError("El movimiento manual debe pertenecer al usuario.")
        if self.importacion_id and self.importacion.user_id != user_id:
            raise ValidationError("La importación debe pertenecer al usuario.")
        fuentes = [self.movimiento_bancario_id, self.movimiento_manual_id, self.importacion_id]
        if sum(source is not None for source in fuentes) != 1:
            raise ValidationError("La asignación debe usar exactamente una fuente de pago.")
        if self.monto_asignado and self.monto_asignado <= 0:
            raise ValidationError("El monto asignado debe ser mayor a cero.")
        fuente, filtro = None, {}
        if self.movimiento_bancario_id:
            fuente = abs(self.movimiento_bancario.monto_clp)
            filtro = {"movimiento_bancario_id": self.movimiento_bancario_id}
        elif self.movimiento_manual_id:
            fuente = abs(self.movimiento_manual.monto_clp)
            filtro = {"movimiento_manual_id": self.movimiento_manual_id}
        elif self.importacion_id:
            try:
                from gastos.services import monto_facturado_clp

                fuente, _ = monto_facturado_clp(self.importacion)
                fuente = abs(fuente)
            except Exception:
                fuente = None
            filtro = {"importacion_id": self.importacion_id}
        if fuente is not None and self.monto_asignado:
            existentes = type(self).objects.filter(**filtro)
            if self.pk:
                existentes = existentes.exclude(pk=self.pk)
            asignado = existentes.aggregate(total=Sum("monto_asignado"))["total"] or Decimal("0")
            if asignado + self.monto_asignado > fuente:
                raise ValidationError({"monto_asignado": "Las asignaciones superan el monto disponible del movimiento."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
