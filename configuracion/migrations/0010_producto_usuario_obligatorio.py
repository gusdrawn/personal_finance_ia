import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def eliminar_productos_huerfanos(apps, schema_editor):
    # 0009 sólo deja sin dueño productos sin ninguna relación financiera.
    apps.get_model("configuracion", "Producto").objects.filter(user__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("configuracion", "0009_productos_por_usuario"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.RunPython(eliminar_productos_huerfanos, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="producto",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="productos_financieros",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
