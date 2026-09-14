import re

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


def _repoint(apps, old, new, user_id):
    relations = (
        ("banca", "MovimientoBancario", "producto", {"user_id": user_id}),
        ("banca", "ImportacionEstadoCuenta", "producto", {"user_id": user_id}),
        ("banca", "SaldoSnapshot", "producto", {"user_id": user_id}),
        ("gastos", "CategoriaIngreso", "producto_asociado", {"user_id": user_id}),
        ("patrimonio", "Pasivo", "producto", {"user_id": user_id}),
        ("prestamos", "Prestamo", "tarjeta_asociada", {"user_id": user_id}),
    )
    for app_label, model_name, field, owner_filter in relations:
        Model = apps.get_model(app_label, model_name)
        Model.objects.filter(**{f"{field}_id": old.pk}, **owner_filter).update(**{field: new})
    Credito = apps.get_model("departamentos", "CreditoHipotecario")
    Credito.objects.filter(producto_id=old.pk, departamento__user_id=user_id).update(producto=new)


def asignar_productos(apps, schema_editor):
    Producto = apps.get_model("configuracion", "Producto")
    Movimiento = apps.get_model("banca", "MovimientoBancario")
    Importacion = apps.get_model("banca", "ImportacionEstadoCuenta")
    Snapshot = apps.get_model("banca", "SaldoSnapshot")
    Categoria = apps.get_model("gastos", "CategoriaIngreso")
    Pasivo = apps.get_model("patrimonio", "Pasivo")
    Prestamo = apps.get_model("prestamos", "Prestamo")
    Credito = apps.get_model("departamentos", "CreditoHipotecario")

    for producto in list(Producto.objects.all()):
        owners = set(Movimiento.objects.filter(producto=producto).values_list("user_id", flat=True))
        owners.update(Importacion.objects.filter(producto=producto).values_list("user_id", flat=True))
        owners.update(Snapshot.objects.filter(producto=producto).values_list("user_id", flat=True))
        owners.update(Categoria.objects.filter(producto_asociado=producto).values_list("user_id", flat=True))
        owners.update(Pasivo.objects.filter(producto=producto).values_list("user_id", flat=True))
        owners.update(Prestamo.objects.filter(tarjeta_asociada=producto).values_list("user_id", flat=True))
        owners.update(Credito.objects.filter(producto=producto).values_list("departamento__user_id", flat=True))
        owners.discard(None)
        if not owners:
            continue
        owners = sorted(owners)
        producto.user_id = owners[0]
        match = re.search(r"(?:•{4}|\*{4}|x{4})\s*(\d{4})", producto.nombre, re.I)
        producto.ultimos4 = match.group(1) if match else ""
        if Importacion.objects.filter(producto=producto).exists():
            producto.origen = "IMPORTACION"
        producto.save(update_fields=["user", "ultimos4", "origen"])
        for owner_id in owners[1:]:
            clone = Producto.objects.create(
                user_id=owner_id, banco_id=producto.banco_id, nombre=producto.nombre,
                tipo=producto.tipo, orden=producto.orden, activo=producto.activo,
                tiene_cupo_usd=producto.tiene_cupo_usd, contabilizar=producto.contabilizar,
                dia_cobro=producto.dia_cobro, ultimos4=producto.ultimos4,
                origen=producto.origen,
            )
            _repoint(apps, producto, clone, owner_id)

    # Los estados importados son canónicos. Fusionar sólo cuando existe un candidato
    # manual inequívoco; los bancos con varias tarjetas quedan para revisión.
    for canonical in Producto.objects.filter(origen="IMPORTACION", tipo="TDC", ultimos4__gt=""):
        candidates = list(
            Producto.objects.filter(
                user_id=canonical.user_id, banco_id=canonical.banco_id, tipo="TDC",
                activo=True, ultimos4="",
            ).exclude(pk=canonical.pk)
        )
        if len(candidates) == 1:
            old = candidates[0]
            _repoint(apps, old, canonical, canonical.user_id)
            old.activo = False
            old.reemplazado_por = canonical
            old.save(update_fields=["activo", "reemplazado_por"])
        elif len(candidates) > 1:
            canonical.requiere_revision = True
            canonical.save(update_fields=["requiere_revision"])
            Producto.objects.filter(pk__in=[item.pk for item in candidates]).update(
                activo=False, requiere_revision=True
            )


class Migration(migrations.Migration):
    dependencies = [
        ("configuracion", "0008_alter_credencialintegracion_provider"),
        ("banca", "0013_importacionestadocuenta_cupo_disponible_usd_and_more"),
        ("gastos", "0009_alter_categoriaingreso_tipo_and_more"),
        ("departamentos", "0003_creditohipotecario_producto_and_more"),
        ("patrimonio", "0003_alter_activo_tipo_cuentaporcobrar_and_more"),
        ("prestamos", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(
            model_name="producto", name="user",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="productos_financieros", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(model_name="producto", name="ultimos4", field=models.CharField(blank=True, db_index=True, max_length=4)),
        migrations.AddField(model_name="producto", name="origen", field=models.CharField(choices=[("MANUAL", "Manual"), ("IMPORTACION", "Importación"), ("CONECTOR", "Conector")], default="MANUAL", max_length=12)),
        migrations.AddField(model_name="producto", name="requiere_revision", field=models.BooleanField(default=False)),
        migrations.AddField(
            model_name="producto", name="reemplazado_por",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="productos_reemplazados", to="configuracion.producto"),
        ),
        migrations.RunPython(asignar_productos, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="producto",
            constraint=models.UniqueConstraint(
                condition=Q(ultimos4__gt=""),
                fields=("user", "banco", "tipo", "ultimos4"),
                name="config_producto_usuario_banco_tipo_ult4",
            ),
        ),
    ]
