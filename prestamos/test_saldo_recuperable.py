from datetime import date, datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from importlib import import_module
import json
import re

from django.apps import apps
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from banca.models import MovimientoBancario
from configuracion.models import Banco, Producto, TipoCambio
from patrimonio.models import Activo
from patrimonio.services.valoracion import resumen_patrimonio
from prestamos.models import Prestamo
from prestamos.views import _registrar_estado
from prestamos.services import sincronizar_activo_prestamo


class SaldoRecuperableTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('saldo_bici')
        self.other = User.objects.create_user('saldo_bici_otra')
        bank = Banco.objects.create(nombre='Banco bicicletas')
        self.product = Producto.objects.create(user=self.user, banco=bank, nombre='Cuenta', tipo='CUENTA')
        TipoCambio.objects.create(fecha=timezone.localdate(), uf=Decimal('40000'), dolar=Decimal('1000'))
        self.loan = Prestamo.objects.create(user=self.user, nombre='Ana', tipo='TERCEROS', monto_total=Decimal('10000'))

    def abono(self, amount='-4000', **kwargs):
        data = dict(user=self.user, producto=self.product, fecha=timezone.localdate(),
                    descripcion='Devolución', monto=Decimal(amount), monto_clp=Decimal(amount),
                    hash_externo=f'pago-{MovimientoBancario.objects.count()}', prestamo=self.loan,
                    naturaleza='TRANSFERENCIA')
        data.update(kwargs)
        return MovimientoBancario.objects.create(**data)

    def test_abono_actualiza_capital_original_cache_y_valoracion(self):
        self.abono()
        activo = Activo.objects.get(pk=self.loan.activo_patrimonial_id)
        self.assertEqual(activo.monto_original, Decimal('6000'))
        self.assertEqual(activo.moneda_original, 'CLP')
        self.assertEqual(activo.monto_clp, Decimal('6000'))
        self.assertEqual(activo.monto_usd, Decimal('6'))
        self.assertEqual(resumen_patrimonio(self.user)['activos_total_clp'], Decimal('6000'))

    def test_editar_desvincular_y_borrar_abono_restituye_solo_su_prestamo(self):
        payment = self.abono()
        payment.monto = payment.monto_clp = Decimal('-2000')
        payment.save()
        self.assertEqual(Activo.objects.get(pk=self.loan.activo_patrimonial_id).monto_original, Decimal('8000'))
        second = Prestamo.objects.create(user=self.user, nombre='Bruno', tipo='TERCEROS', monto_total=Decimal('7000'))
        payment.prestamo = second
        payment.save()
        self.assertEqual(Activo.objects.get(pk=self.loan.activo_patrimonial_id).monto_original, Decimal('10000'))
        self.assertEqual(Activo.objects.get(pk=second.activo_patrimonial_id).monto_original, Decimal('5000'))
        payment.delete()
        self.assertEqual(Activo.objects.get(pk=second.activo_patrimonial_id).monto_original, Decimal('7000'))

    def test_no_suma_abonos_futuros_descartados_ajenos_usd_y_no_baja_de_cero(self):
        self.abono(fecha=timezone.localdate() + timedelta(days=1))
        self.abono(estado='DESCARTADO_CIERRE')
        self.abono(por_revisar=True)
        self.abono(user=self.other)
        self.abono(moneda='USD')
        self.assertEqual(self.loan.saldo_recuperable(), Decimal('10000'))
        self.abono('-12000')
        self.assertEqual(self.loan.saldo_recuperable(), Decimal('0'))

    def test_nombres_iguales_renombre_y_borrado_conservan_identidad(self):
        second = Prestamo.objects.create(user=self.user, nombre='Ana', tipo='TERCEROS', monto_total=Decimal('5000'))
        self.assertNotEqual(self.loan.activo_patrimonial_id, second.activo_patrimonial_id)
        self.loan.nombre = 'Ana nueva'
        self.loan.save()
        self.loan.delete()
        first_asset = Activo.objects.get(nombre='Bicicleta - Ana nueva')
        self.assertFalse(first_asset.activo)
        self.assertTrue(Activo.objects.get(pk=second.activo_patrimonial_id).activo)

    def test_migracion_enlaza_solo_el_autogenerado_inequivoco(self):
        original = self.loan.activo_patrimonial_id
        Prestamo.objects.filter(pk=self.loan.pk).update(activo_patrimonial=None)
        self.abono()
        schema_editor = type('Schema', (), {'connection': connection})()
        import_module('prestamos.migrations.0003_activo_patrimonial').enlazar_activos_inequivocos(apps, schema_editor)
        self.loan.refresh_from_db()
        self.assertEqual(self.loan.activo_patrimonial_id, original)
        self.assertEqual(Activo.objects.get(pk=original).monto_original, Decimal('6000'))
        second = Prestamo.objects.create(user=self.other, nombre='Ana', tipo='TERCEROS', monto_total=Decimal('100'))
        self.assertNotEqual(second.activo_patrimonial_id, original)

    def test_activo_de_otro_usuario_no_puede_ser_sincronizado(self):
        foreign = Activo.objects.create(user=self.other, nombre='Privado', tipo='OTRO', monto_clp=Decimal('100'))
        Prestamo.objects.filter(pk=self.loan.pk).update(activo_patrimonial=foreign)
        with self.assertRaises(ValueError):
            sincronizar_activo_prestamo(self.loan)
        foreign.refresh_from_db()
        self.assertEqual(foreign.monto_clp, Decimal('100'))

    def test_activo_generado_se_edita_desde_su_fuente(self):
        self.client.force_login(self.user)
        self.client.post(reverse('editar_activo', args=[self.loan.activo_patrimonial_id]), {'monto_original': '999999'})
        self.client.post(reverse('borrar_activo', args=[self.loan.activo_patrimonial_id]))
        self.assertEqual(Activo.objects.get(pk=self.loan.activo_patrimonial_id).monto_original, Decimal('10000'))

    def test_nombres_de_graficos_usan_json_y_no_rompen_el_limite_de_script(self):
        nombre = "O'Connor \\ \"Ana\" </script><script>alert(1)</script>"
        self.loan.nombre = nombre
        self.loan.save()
        self.client.force_login(self.user)
        response = self.client.get(reverse('prestamos_index'))
        html = response.content.decode()
        payload = re.search(r'<script id="nombres-terceros" type="application/json">(.*?)</script>', html, re.S).group(1)
        self.assertEqual(json.loads(payload), [nombre])
        self.assertNotIn('</script>', payload)
        self.assertNotIn('labels: [', html)

    def test_saldo_confirmado_no_descuenta_dos_veces_abonos_anteriores_o_importados_tarde(self):
        ayer = timezone.localdate() - timedelta(days=1)
        self.abono(fecha=ayer)
        _registrar_estado(self.loan, ayer, Decimal('6000'), True, 'Saldo confirmado')
        self.assertEqual(self.loan.saldo_recuperable(), Decimal('6000'))
        self.abono('-1000', fecha=ayer)
        self.assertEqual(self.loan.saldo_recuperable(), Decimal('6000'))
        self.abono('-1000')
        self.assertEqual(self.loan.saldo_recuperable(), Decimal('5000'))

    def test_valoracion_lee_la_fuente_incluso_tras_una_actualizacion_masiva(self):
        payment = self.abono()
        MovimientoBancario.objects.filter(pk=payment.pk).update(monto=Decimal('-1000'), monto_clp=Decimal('-1000'))
        self.assertEqual(resumen_patrimonio(self.user)['activos_total_clp'], Decimal('9000'))

    def test_migracion_y_sync_no_adivinan_entre_nombres_legacy_iguales(self):
        second = Prestamo.objects.create(user=self.user, nombre='Ana', tipo='TERCEROS', monto_total=Decimal('5000'))
        ids = [self.loan.pk, second.pk]
        Prestamo.objects.filter(pk__in=ids).update(activo_patrimonial=None)
        schema_editor = type('Schema', (), {'connection': connection})()
        import_module('prestamos.migrations.0003_activo_patrimonial').enlazar_activos_inequivocos(apps, schema_editor)
        self.assertFalse(Prestamo.objects.filter(pk__in=ids, activo_patrimonial__isnull=False).exists())
        self.assertIsNone(sincronizar_activo_prestamo(self.loan))
        self.assertEqual(Activo.objects.filter(user=self.user).count(), 2)
        self.assertEqual(Activo.objects.filter(user=self.user, requiere_revision_fuente=True).count(), 2)

    def test_fecha_de_inicio_usa_el_dia_local_de_chile(self):
        self.loan.created_at = datetime(2026, 7, 2, 1, 0, tzinfo=datetime_timezone.utc)
        self.assertEqual(self.loan.saldo_recuperable(date(2026, 7, 1)), Decimal('10000'))
