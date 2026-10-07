from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("departamentos", "0003_creditohipotecario_producto_and_more")]

    operations = [
        migrations.AddField(
            model_name="departamento",
            name="comuna",
            field=models.CharField(blank=True, help_text="Comuna de la propiedad para obligaciones municipales", max_length=100),
        ),
        migrations.AddField(
            model_name="departamento",
            name="rol_sii",
            field=models.CharField(blank=True, help_text="Rol SII o rol municipal", max_length=40),
        ),
        migrations.AddField(
            model_name="departamento",
            name="url_pago_aseo",
            field=models.URLField(blank=True, help_text="Enlace editable al portal de pago de Aseo"),
        ),
    ]
