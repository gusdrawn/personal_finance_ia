from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("banca", "0015_separar_hipotecas_y_pagos_terceros")]

    operations = [
        migrations.AddField(
            model_name="importacionestadocuenta",
            name="numero_producto",
            field=models.CharField(blank=True, max_length=40),
        ),
        migrations.AddField(
            model_name="importacionestadocuenta",
            name="saldo_final_clp",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True),
        ),
        migrations.AddField(
            model_name="importacionestadocuenta",
            name="tipo_producto",
            field=models.CharField(default="TDC", max_length=20),
        ),
        migrations.AddField(
            model_name="saldosnapshot",
            name="saldo_disponible_clp",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Saldo disponible de una cuenta corriente.",
                max_digits=15,
                null=True,
            ),
        ),
    ]
