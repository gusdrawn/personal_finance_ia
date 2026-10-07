import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("banca", "0021_referencia_externa_movimientos")]
    operations = [
        migrations.AddField(
            model_name="importacionestadocuenta", name="movimiento_pago",
            field=models.ForeignKey(null=True, blank=True, on_delete=django.db.models.deletion.PROTECT, related_name="comprobantes_pago", help_text="Cargo que respalda el comprobante de pago municipal.", to="banca.movimientobancario"),
        ),
    ]
