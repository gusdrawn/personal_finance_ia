from decimal import Decimal

from django.db import migrations, models
from django.db.models import Sum
import django.db.models.deletion
from django.utils import timezone


def enlazar_activos_inequivocos(apps, schema_editor):
    Prestamo = apps.get_model('prestamos', 'Prestamo')
    Activo = apps.get_model('patrimonio', 'Activo')
    Movimiento = apps.get_model('banca', 'MovimientoBancario')
    TipoCambio = apps.get_model('configuracion', 'TipoCambio')
    db = schema_editor.connection.alias
    hoy = timezone.localdate()
    cambio = TipoCambio.objects.using(db).filter(fuente='mindicador.cl', fecha__lte=hoy).order_by('-fecha', '-id').first()
    for prestamo in Prestamo.objects.using(db).filter(tipo='TERCEROS'):
        # A repeated name is insufficient evidence to bind an existing asset.
        if Prestamo.objects.using(db).filter(user_id=prestamo.user_id, tipo='TERCEROS', nombre=prestamo.nombre).count() != 1:
            continue
        candidatos = Activo.objects.using(db).filter(
            user_id=prestamo.user_id, tipo='PRESTAMO_DADO',
            nombre=f'Bicicleta - {prestamo.nombre}',
            notas=f'Auto-generado desde Bicicleta: {prestamo.nombre}',
            fuente_externa='', departamento__isnull=True,
            cuenta_por_cobrar__isnull=True,
        )
        if candidatos.count() != 1:
            continue
        activo = candidatos.get()
        devuelto = Movimiento.objects.using(db).filter(
            prestamo_id=prestamo.pk, user_id=prestamo.user_id, producto__user_id=prestamo.user_id,
            moneda='CLP', monto__lt=0, fecha__lte=hoy, por_revisar=False,
        ).exclude(estado='DESCARTADO_CIERRE').aggregate(total=Sum('monto'))['total'] or Decimal('0')
        saldo = max(prestamo.monto_total + devuelto, Decimal('0'))
        Activo.objects.using(db).filter(pk=activo.pk).update(
            monto_original=saldo, moneda_original='CLP', monto_clp=saldo,
            monto_usd=(saldo / cambio.dolar).quantize(Decimal('0.01')) if cambio and cambio.dolar else Decimal('0'),
            requiere_revision_moneda=False, activo=prestamo.activo,
        )
        Prestamo.objects.using(db).filter(pk=prestamo.pk).update(activo_patrimonial_id=activo.pk)


class Migration(migrations.Migration):
    dependencies = [
        ('prestamos', '0002_historialprestamo'),
        ('patrimonio', '0007_activo_cuenta_fuente_activo_fuente_externa_and_more'),
        ('banca', '0022_importacion_movimiento_pago'),
    ]
    operations = [
        migrations.AddField(
            model_name='historialprestamo', name='es_saldo_confirmado',
            field=models.BooleanField(default=False, help_text='Saldo al cierre de la fecha; incluye abonos anteriores.'),
        ),
        migrations.AddField(
            model_name='prestamo', name='activo_patrimonial',
            field=models.OneToOneField(blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='prestamo_recuperable', to='patrimonio.activo'),
        ),
        migrations.RunPython(enlazar_activos_inequivocos, migrations.RunPython.noop),
    ]
