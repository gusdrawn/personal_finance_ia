import django.core.validators
import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gastos", "0009_alter_categoriaingreso_tipo_and_more"),
        ("banca", "0014_gastos_unificados"),
        ("departamentos", "0003_creditohipotecario_producto_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="MovimientoManual",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fecha", models.DateField(db_index=True)),
                ("periodo", models.DateField(db_index=True, help_text="Primer día del mes contable")),
                ("direccion", models.CharField(choices=[("INGRESO", "Ingreso"), ("EGRESO", "Egreso")], max_length=8)),
                ("monto", models.DecimalField(decimal_places=2, max_digits=15, validators=[django.core.validators.MinValueValidator(Decimal("0.01"))])),
                ("moneda", models.CharField(choices=[("CLP", "Chilean Peso"), ("USD", "US Dollar"), ("UF", "Unidad de Fomento")], default="CLP", max_length=3)),
                ("monto_clp", models.DecimalField(decimal_places=2, max_digits=15)),
                ("tipo_cambio", models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True)),
                ("monto_clp_estimado", models.BooleanField(default=False)),
                ("naturaleza", models.CharField(choices=[("OPERACIONAL", "Operacional"), ("ACTIVO_RECUPERABLE", "Conversión a activo recuperable"), ("INVERSION", "Inversión"), ("TRANSFERENCIA_INTERNA", "Transferencia interna")], default="OPERACIONAL", max_length=24)),
                ("descripcion", models.CharField(max_length=255)),
                ("notas", models.TextField(blank=True)),
                ("es_resumen_mensual", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("categoria", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="movimientos_manuales", to="banca.categoriatematica")),
                ("departamento", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="movimientos_manuales", to="departamentos.departamento")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="movimientos_manuales", to=settings.AUTH_USER_MODEL)),
            ],
            options={"verbose_name": "Movimiento manual", "verbose_name_plural": "Movimientos manuales", "ordering": ["-fecha", "-id"]},
        ),
        migrations.AddIndex(model_name="movimientomanual", index=models.Index(fields=["user", "periodo", "direccion"], name="gastos_movi_user_id_73f1cd_idx")),
        migrations.AddIndex(model_name="movimientomanual", index=models.Index(fields=["user", "categoria", "periodo"], name="gastos_movi_user_id_e78168_idx")),
        migrations.AddConstraint(
            model_name="movimientomanual",
            constraint=models.UniqueConstraint(condition=models.Q(es_resumen_mensual=True), fields=("user", "periodo", "categoria", "direccion"), name="gastos_resumen_manual_categoria_mes_un"),
        ),
        migrations.DeleteModel(name="RegistroMensual"),
        migrations.DeleteModel(name="CategoriaIngreso"),
    ]
