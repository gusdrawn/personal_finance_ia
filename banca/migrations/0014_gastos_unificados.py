import django.db.models.deletion
from decimal import Decimal
from django.db import migrations, models


def preparar_estados(apps, schema_editor):
    Importacion = apps.get_model("banca", "ImportacionEstadoCuenta")
    TipoCambio = apps.get_model("configuracion", "TipoCambio")
    vistos = set()
    for item in Importacion.objects.filter(estado="PROCESADO").order_by("-created_at", "-id"):
        if not item.fecha_vencimiento or not item.producto_id:
            continue
        key = (item.user_id, item.producto_id, item.moneda, item.fecha_vencimiento.year, item.fecha_vencimiento.month)
        item.vigente = key not in vistos
        vistos.add(key)
        campos = ["vigente"]
        if item.moneda == "USD" and item.monto_facturado is not None:
            cambio = TipoCambio.objects.filter(fecha__lte=item.fecha_vencimiento).order_by("-fecha", "-id").first()
            cambio = cambio or TipoCambio.objects.order_by("-fecha", "-id").first()
            if cambio:
                item.tipo_cambio_estimado = cambio.dolar
                item.monto_facturado_clp_estimado = (item.monto_facturado * cambio.dolar).quantize(Decimal("1"))
                campos += ["tipo_cambio_estimado", "monto_facturado_clp_estimado"]
        item.save(update_fields=campos)


class Migration(migrations.Migration):
    dependencies = [("banca", "0013_importacionestadocuenta_cupo_disponible_usd_and_more"), ("configuracion", "0009_productos_por_usuario")]
    operations = [
        migrations.AddField(
            model_name="categoriatematica", name="aplica_a",
            field=models.CharField(choices=[("INGRESO", "Ingreso"), ("EGRESO", "Egreso"), ("AMBOS", "Ambos")], default="EGRESO", max_length=8),
        ),
        migrations.AddField(
            model_name="categoriatematica", name="origen",
            field=models.CharField(choices=[("SISTEMA", "Sistema"), ("IA", "IA"), ("USUARIO", "Usuario")], default="SISTEMA", max_length=8),
        ),
        migrations.AddField(model_name="importacionestadocuenta", name="vigente", field=models.BooleanField(db_index=True, default=True)),
        migrations.AddField(
            model_name="importacionestadocuenta", name="reemplaza_a",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="correcciones", to="banca.importacionestadocuenta"),
        ),
        migrations.AddField(model_name="importacionestadocuenta", name="monto_facturado_clp_estimado", field=models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True)),
        migrations.AddField(model_name="importacionestadocuenta", name="tipo_cambio_estimado", field=models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True)),
        migrations.AddField(model_name="importacionestadocuenta", name="monto_pagado_clp", field=models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True)),
        migrations.AddField(model_name="importacionestadocuenta", name="fecha_pago_real", field=models.DateField(blank=True, null=True)),
        migrations.AddField(
            model_name="eventofinancieroexterno", name="categoria",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="eventos_externos", to="banca.categoriatematica"),
        ),
        migrations.AddField(
            model_name="eventofinancieroexterno", name="fuente_clasificacion",
            field=models.CharField(choices=[("PENDIENTE", "Sin clasificar"), ("IA", "IA"), ("REGLA", "Regla aprendida"), ("MANUAL", "Manual")], default="PENDIENTE", max_length=10),
        ),
        migrations.AddField(model_name="eventofinancieroexterno", name="categoria_es_manual", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="eventofinancieroexterno", name="confianza_ia", field=models.DecimalField(blank=True, decimal_places=3, max_digits=4, null=True)),
        migrations.RunPython(preparar_estados, migrations.RunPython.noop),
    ]
