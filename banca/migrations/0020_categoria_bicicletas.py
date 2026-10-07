from django.db import migrations


def agregar_categoria(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Categoria = apps.get_model("banca", "CategoriaTematica")
    for user_id in User.objects.values_list("pk", flat=True).iterator():
        Categoria.objects.get_or_create(
            user_id=user_id, nombre="Bicicletas",
            defaults={"aplica_a": "AMBOS", "origen": "SISTEMA", "esencialidad_defecto": "FIJO"},
        )


class Migration(migrations.Migration):
    dependencies = [("banca", "0019_configuracionbanca_presupuesto_mensual_clp")]
    operations = [migrations.RunPython(agregar_categoria, migrations.RunPython.noop)]
