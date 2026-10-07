from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("banca", "0017_revision_movimientos_ambiguos"),
        ("departamentos", "0004_departamento_obligaciones"),
    ]

    operations = [
        migrations.AddField(
            model_name="importacionestadocuenta",
            name="tipo_documento",
            field=models.CharField(choices=[("ESTADO_CUENTA", "Estado de cuenta"), ("ORDEN_ASEO", "Orden de ingreso de Aseo"), ("PAGO_ASEO", "Certificado de pago de Aseo")], default="ESTADO_CUENTA", max_length=20),
        ),
        migrations.AddField(
            model_name="importacionestadocuenta",
            name="departamento",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="importaciones_aseo", to="departamentos.departamento"),
        ),
        migrations.AddField(
            model_name="importacionestadocuenta",
            name="documento_referencia",
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
