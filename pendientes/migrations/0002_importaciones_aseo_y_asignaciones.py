from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("banca", "0018_importacion_documentos_municipales"),
        ("gastos", "0010_movimientos_manuales"),
        ("pendientes", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="ocurrenciapendiente",
            name="importacion",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ocurrencias_pendientes",
                to="banca.importacionestadocuenta",
            ),
        ),
        migrations.AddField(
            model_name="asignacionpagopendiente",
            name="importacion",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="asignaciones_pendientes",
                to="banca.importacionestadocuenta",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="asignacionpagopendiente",
            name="pendiente_asignacion_un_solo_movimiento",
        ),
        migrations.AddConstraint(
            model_name="asignacionpagopendiente",
            constraint=models.CheckConstraint(
                condition=(
                    (
                        models.Q(movimiento_bancario__isnull=False)
                        & models.Q(movimiento_manual__isnull=True)
                        & models.Q(importacion__isnull=True)
                    )
                    | (
                        models.Q(movimiento_bancario__isnull=True)
                        & models.Q(movimiento_manual__isnull=False)
                        & models.Q(importacion__isnull=True)
                    )
                    | (
                        models.Q(movimiento_bancario__isnull=True)
                        & models.Q(movimiento_manual__isnull=True)
                        & models.Q(importacion__isnull=False)
                    )
                ),
                name="pendiente_asignacion_un_solo_movimiento",
            ),
        ),
        migrations.AddConstraint(
            model_name="asignacionpagopendiente",
            constraint=models.UniqueConstraint(
                condition=models.Q(importacion__isnull=False),
                fields=("ocurrencia", "importacion"),
                name="pendiente_asignacion_importacion_unica",
            ),
        ),
    ]
