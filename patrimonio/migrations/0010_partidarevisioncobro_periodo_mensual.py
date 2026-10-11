from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("patrimonio", "0009_cobros_periodos_y_vista_mensual"),
    ]

    operations = [
        migrations.AddField(
            model_name="partidarevisioncobro",
            name="periodo_mensual",
            field=models.DateField(
                blank=True, null=True,
                help_text="Mes de la reconstrucción provisional, independiente del mes de confirmación.",
            ),
        ),
    ]
