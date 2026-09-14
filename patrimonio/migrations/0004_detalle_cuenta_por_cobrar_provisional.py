from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("patrimonio", "0003_alter_activo_tipo_cuentaporcobrar_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="detallecuentaporcobrar",
            name="provisional",
            field=models.BooleanField(
                default=False,
                help_text="El cargo ya ocurrió, pero su fuente bancaria aún no fue importada.",
            ),
        ),
    ]
