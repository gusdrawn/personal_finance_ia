from django.db import migrations, models


def revisar_confirmaciones_automaticas(apps, schema_editor):
    # Las decisiones explícitas se preservan. Las confirmaciones automáticas
    # antiguas sólo tenían evidencia de fecha e importe y necesitan revisión.
    apps.get_model("gastos", "ConciliacionMovimientoManual").objects.filter(
        estado="CONFIRMADA", confirmada_manual=False,
    ).update(estado="SUGERIDA")


class Migration(migrations.Migration):
    dependencies = [("gastos", "0011_conciliacionmovimientomanual")]

    operations = [
        migrations.AddField(
            model_name="movimientomanual", name="modo_resumen",
            field=models.CharField(max_length=10, choices=[("TOTAL", "Total del mes"), ("ADICIONAL", "Importe adicional")], default="TOTAL", help_text="TOTAL incluye lo ya registrado en la categoría; ADICIONAL se suma."),
        ),
        migrations.RunPython(revisar_confirmaciones_automaticas, migrations.RunPython.noop),
    ]
