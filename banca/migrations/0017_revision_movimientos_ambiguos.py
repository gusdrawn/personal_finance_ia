from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("banca", "0016_cartolas_cuenta_corriente")]

    operations = [
        migrations.AlterField(
            model_name="movimientobancario",
            name="por_revisar",
            field=models.BooleanField(
                default=False,
                help_text="Movimiento ambiguo: confirmar si es ingreso, gasto o transferencia",
            ),
        ),
    ]
