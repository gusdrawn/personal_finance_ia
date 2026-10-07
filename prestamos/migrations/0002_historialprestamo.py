from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


def registrar_saldos_vigentes(apps, schema_editor):
    Prestamo = apps.get_model("prestamos", "Prestamo")
    Historial = apps.get_model("prestamos", "HistorialPrestamo")
    hoy = timezone.localdate()
    for prestamo in Prestamo.objects.all().iterator():
        Historial.objects.create(
            prestamo_id=prestamo.pk,
            fecha=hoy,
            monto_total=prestamo.monto_total,
            activo=prestamo.activo,
            motivo="Saldo vigente al activar el historial; los meses anteriores requieren un registro manual.",
        )


class Migration(migrations.Migration):
    dependencies = [("prestamos", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="HistorialPrestamo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fecha", models.DateField(db_index=True)),
                ("monto_total", models.DecimalField(decimal_places=2, max_digits=15)),
                ("activo", models.BooleanField(default=True)),
                ("motivo", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("prestamo", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="historial", to="prestamos.prestamo")),
            ],
            options={"ordering": ["-fecha", "-id"], "indexes": [models.Index(fields=["prestamo", "fecha"], name="prestamos_h_prestam_95dacc_idx")]},
        ),
        migrations.RunPython(registrar_saldos_vigentes, migrations.RunPython.noop),
    ]
