import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("banca", "0007_movimientobancario_hash_user"),
    ]

    operations = [
        migrations.CreateModel(
            name="ApplePayApiKey",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("nombre", models.CharField(default="Atajo iPhone", max_length=80)),
                (
                    "prefijo",
                    models.CharField(
                        help_text="Prefijo no secreto para identificar la llave sin mostrarla completa.",
                        max_length=16,
                    ),
                ),
                ("clave_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("activo", models.BooleanField(default=True)),
                ("ultimo_uso_at", models.DateTimeField(blank=True, null=True)),
                ("revocado_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="apple_pay_api_keys",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "API key de Apple Pay",
                "verbose_name_plural": "API keys de Apple Pay",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="ApplePayEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("event_id", models.CharField(max_length=120)),
                ("payload_hash", models.CharField(max_length=64)),
                (
                    "estado",
                    models.CharField(
                        choices=[
                            ("PROCESADO", "Procesado"),
                            ("DUPLICADO", "Duplicado"),
                            ("ERROR", "Error"),
                        ],
                        default="PROCESADO",
                        max_length=12,
                    ),
                ),
                ("respuesta", models.JSONField(blank=True, default=dict)),
                ("error", models.TextField(blank=True)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "api_key",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="eventos",
                        to="banca.applepayapikey",
                    ),
                ),
                (
                    "movimiento",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="apple_pay_eventos",
                        to="banca.movimientobancario",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="apple_pay_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Evento Apple Pay",
                "verbose_name_plural": "Eventos Apple Pay",
                "ordering": ["-received_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="applepayevent",
            constraint=models.UniqueConstraint(
                fields=("user", "event_id"), name="banca_apple_pay_event_user_id_unico"
            ),
        ),
    ]
