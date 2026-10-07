from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion
from decimal import Decimal


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("banca", "0017_revision_movimientos_ambiguos"),
        ("configuracion", "0010_producto_usuario_obligatorio"),
        ("departamentos", "0004_departamento_obligaciones"),
        ("gastos", "0010_movimientos_manuales"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReglaPendiente",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre", models.CharField(max_length=180)),
                ("descripcion", models.TextField(blank=True)),
                ("tipo", models.CharField(choices=[("PAGO", "Pago"), ("DOCUMENTO", "Documento"), ("SINCRONIZACION", "Sincronización"), ("MANUAL", "Manual")], default="MANUAL", max_length=20)),
                ("clave_fuente", models.CharField(blank=True, default="", help_text="Clave idempotente para reglas automáticas.", max_length=180)),
                ("periodicidad", models.CharField(choices=[("UNICA", "Una vez"), ("MENSUAL", "Mensual"), ("BIMESTRAL", "Bimestral"), ("TRIMESTRAL", "Trimestral"), ("SEMESTRAL", "Semestral"), ("ANUAL", "Anual"), ("MESES", "Meses configurables"), ("DESDE_ULTIMO_REGISTRO", "Desde el último registro")], default="UNICA", max_length=28)),
                ("fecha_inicio", models.DateField(blank=True, null=True)),
                ("dia_vencimiento", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("meses_vencimiento", models.JSONField(blank=True, default=list)),
                ("intervalo_dias", models.PositiveSmallIntegerField(default=30)),
                ("monto_estimado", models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True, validators=[django.core.validators.MinValueValidator(0)])),
                ("moneda", models.CharField(default="CLP", max_length=3)),
                ("url_pago", models.URLField(blank=True)),
                ("canal_cobro", models.CharField(choices=[("DIRECTO", "Cobro directo"), ("INCLUIDO_CONTRIBUCIONES", "Incluido en contribuciones"), ("DESCONOCIDO", "Por confirmar")], default="DESCONOCIDO", max_length=32)),
                ("recordar_dias", models.PositiveSmallIntegerField(default=3)),
                ("umbral_dias_inactividad", models.PositiveSmallIntegerField(default=30)),
                ("contabiliza_gasto", models.BooleanField(default=True)),
                ("automatica", models.BooleanField(default=False)),
                ("activa", models.BooleanField(default=True)),
                ("creada_at", models.DateTimeField(auto_now_add=True)),
                ("actualizada_at", models.DateTimeField(auto_now=True)),
                ("departamento", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="reglas_pendientes", to="departamentos.departamento")),
                ("producto", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="reglas_pendientes", to="configuracion.producto")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reglas_pendientes", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["nombre", "fecha_inicio"], "indexes": [models.Index(fields=["user", "activa", "tipo"], name="pend_regla_user_activa_tipo"), models.Index(fields=["user", "departamento", "activa"], name="pend_regla_user_depto_activa")]},
        ),
        migrations.CreateModel(
            name="OcurrenciaPendiente",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fecha_vencimiento", models.DateField(db_index=True)),
                ("monto_esperado", models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True)),
                ("estado", models.CharField(choices=[("PENDIENTE", "Pendiente"), ("COMPLETADA", "Completada"), ("NO_APLICA", "No aplica"), ("SIN_CARTOLA", "Sin cartola"), ("SIN_MOVIMIENTOS", "Sin movimientos"), ("INCLUIDA_EN_OTRO_PAGO", "Incluida en otro pago")], db_index=True, default="PENDIENTE", max_length=28)),
                ("fecha_real", models.DateField(blank=True, null=True)),
                ("monto_real", models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True)),
                ("nota_resolucion", models.TextField(blank=True)),
                ("completada_at", models.DateTimeField(blank=True, null=True)),
                ("creada_at", models.DateTimeField(auto_now_add=True)),
                ("actualizada_at", models.DateTimeField(auto_now=True)),
                ("regla", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ocurrencias", to="pendientes.reglapendiente")),
            ],
            options={"ordering": ["estado", "fecha_vencimiento", "regla__nombre"], "indexes": [models.Index(fields=["regla", "estado", "fecha_vencimiento"], name="pend_ocur_regla_estado_fecha"), models.Index(fields=["fecha_vencimiento", "estado"], name="pend_ocur_fecha_estado")]},
        ),
        migrations.CreateModel(
            name="AsignacionPagoPendiente",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("monto_asignado", models.DecimalField(decimal_places=2, max_digits=15, validators=[django.core.validators.MinValueValidator(Decimal("0.01"))])),
                ("referencia_pago", models.CharField(blank=True, max_length=120)),
                ("creada_at", models.DateTimeField(auto_now_add=True)),
                ("movimiento_bancario", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="asignaciones_pendientes", to="banca.movimientobancario")),
                ("movimiento_manual", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="asignaciones_pendientes", to="gastos.movimientomanual")),
                ("ocurrencia", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="asignaciones_pago", to="pendientes.ocurrenciapendiente")),
            ],
            options={"ordering": ["-creada_at"]},
        ),
        migrations.AddConstraint(model_name="reglapendiente", constraint=models.UniqueConstraint(condition=~models.Q(("clave_fuente", "")), fields=("user", "clave_fuente"), name="pendiente_regla_user_clave_unica")),
        migrations.AddConstraint(model_name="ocurrenciapendiente", constraint=models.UniqueConstraint(fields=("regla", "fecha_vencimiento"), name="pendiente_ocurrencia_regla_fecha_unica")),
        migrations.AddConstraint(model_name="asignacionpagopendiente", constraint=models.CheckConstraint(condition=(models.Q(movimiento_bancario__isnull=False, movimiento_manual__isnull=True) | models.Q(movimiento_bancario__isnull=True, movimiento_manual__isnull=False)), name="pendiente_asignacion_un_solo_movimiento")),
        migrations.AddConstraint(model_name="asignacionpagopendiente", constraint=models.UniqueConstraint(condition=models.Q(movimiento_bancario__isnull=False), fields=("ocurrencia", "movimiento_bancario"), name="pendiente_asignacion_banco_unica")),
        migrations.AddConstraint(model_name="asignacionpagopendiente", constraint=models.UniqueConstraint(condition=models.Q(movimiento_manual__isnull=False), fields=("ocurrencia", "movimiento_manual"), name="pendiente_asignacion_manual_unica")),
    ]
