import datetime
import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


def migrar_origen_movimientos(apps, schema_editor):
    Movimiento = apps.get_model("banca", "MovimientoBancario")
    Importacion = apps.get_model("banca", "ImportacionEstadoCuenta")
    ApplePayEvent = apps.get_model("banca", "ApplePayEvent")

    ids_pdf = Importacion.objects.values_list("movimientos__id", flat=True)
    Movimiento.objects.filter(pk__in=ids_pdf).update(origen="PDF")
    for evento in ApplePayEvent.objects.select_related("movimiento").iterator():
        movimiento = evento.movimiento
        if not movimiento or movimiento.importaciones.exists():
            continue
        if movimiento.estado == "NO_FACTURADO":
            Movimiento.objects.filter(pk=movimiento.pk).update(
                origen="APPLE_PAY",
                estado="PROVISIONAL",
                provisional_hasta=movimiento.fecha + datetime.timedelta(days=45),
            )


class Migration(migrations.Migration):
    dependencies = [
        ("banca", "0011_fuentes_externas_houm_neat"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(model_name="importacionestadocuenta", name="provisionales_ambiguos", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="importacionestadocuenta", name="provisionales_confirmados", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="importacionestadocuenta", name="provisionales_descartados", field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name="movimientobancario", name="cerrado_por_importacion", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="provisionales_cerrados", to="banca.importacionestadocuenta")),
        migrations.AddField(model_name="movimientobancario", name="motivo_cierre", field=models.CharField(blank=True, max_length=80)),
        migrations.AddField(model_name="movimientobancario", name="origen", field=models.CharField(choices=[("APPLE_PAY", "Apple Pay"), ("PDF", "Estado de cuenta PDF"), ("CONECTOR", "Conector bancario")], db_index=True, default="CONECTOR", max_length=12)),
        migrations.AddField(model_name="movimientobancario", name="provisional_hasta", field=models.DateField(blank=True, help_text="Fecha orientativa para advertir que aún falta el estado de cuenta.", null=True)),
        migrations.AlterField(model_name="movimientobancario", name="estado", field=models.CharField(choices=[("PROVISIONAL", "Provisional (Apple Pay)"), ("NO_FACTURADO", "No facturado"), ("FACTURADO", "Facturado"), ("PAGADO", "Pagado"), ("DESCARTADO_CIERRE", "Descartado al cierre")], db_index=True, default="NO_FACTURADO", max_length=20)),
        migrations.CreateModel(name="ConfiguracionNotificaciones", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("telegram_activo", models.BooleanField(default=False)), ("telegram_bot_token_cifrado", models.BinaryField(blank=True, null=True)),
            ("telegram_chat_id_cifrado", models.BinaryField(blank=True, null=True)), ("telegram_token_prefijo", models.CharField(blank=True, max_length=12)),
            ("avisar_gasto_inmediato", models.BooleanField(default=True)), ("monto_minimo_gasto", models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=15)),
            ("resumen_diario", models.BooleanField(default=False)), ("hora_resumen_diario", models.TimeField(default=datetime.time(21, 0))),
            ("resumen_importacion_pdf", models.BooleanField(default=True)), ("avisar_ocio", models.BooleanField(default=True)),
            ("avisar_limites_categoria", models.BooleanField(default=True)), ("avisar_vencimientos", models.BooleanField(default=True)),
            ("dias_anticipacion_vencimientos", models.PositiveSmallIntegerField(default=3)), ("avisar_provisional_atrasado", models.BooleanField(default=True)),
            ("dias_provisional_atrasado", models.PositiveSmallIntegerField(default=45)), ("zona_horaria", models.CharField(default="America/Santiago", max_length=50)),
            ("ultima_prueba_at", models.DateTimeField(blank=True, null=True)), ("ultima_prueba_exitosa", models.BooleanField(default=False)),
            ("updated_at", models.DateTimeField(auto_now=True)),
            ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="config_notificaciones", to=settings.AUTH_USER_MODEL)),
        ], options={"verbose_name": "Configuración de notificaciones", "verbose_name_plural": "Configuraciones de notificaciones"}),
        migrations.CreateModel(name="LimiteCategoria", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("monto_mensual_clp", models.DecimalField(decimal_places=2, max_digits=15)), ("activo", models.BooleanField(default=True)),
            ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ("categoria", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="limites_mensuales", to="banca.categoriatematica")),
            ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="limites_categoria_banca", to=settings.AUTH_USER_MODEL)),
        ], options={"ordering": ["categoria__nombre"], "constraints": [models.UniqueConstraint(fields=("user", "categoria"), name="banca_limite_categoria_user_categoria_unico")]}),
        migrations.CreateModel(name="RegistroNotificacion", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("tipo", models.CharField(max_length=40)), ("canal", models.CharField(default="TELEGRAM", max_length=20)),
            ("clave_idempotencia", models.CharField(max_length=180)),
            ("estado", models.CharField(choices=[("PENDIENTE", "Pendiente"), ("ENVIADO", "Enviado"), ("ERROR", "Error")], default="PENDIENTE", max_length=10)),
            ("contenido", models.TextField(blank=True)), ("error", models.TextField(blank=True)), ("intentos", models.PositiveSmallIntegerField(default=0)),
            ("enviado_at", models.DateTimeField(blank=True, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ("movimiento", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="registros_notificacion", to="banca.movimientobancario")),
            ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notificaciones_banca", to=settings.AUTH_USER_MODEL)),
        ], options={"ordering": ["-created_at"], "constraints": [models.UniqueConstraint(fields=("user", "canal", "clave_idempotencia"), name="banca_notificacion_user_canal_clave_unico")]}),
        migrations.RunPython(migrar_origen_movimientos, migrations.RunPython.noop),
    ]
