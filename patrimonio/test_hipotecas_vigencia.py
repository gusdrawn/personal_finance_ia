from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from banca.models import SaldoSnapshot
from configuracion.models import Banco, Producto, TipoCambio
from departamentos.models import CreditoHipotecario, Departamento
from patrimonio.models import Pasivo, SnapshotPatrimonio
from patrimonio.services.valoracion import resumen_patrimonio


class HipotecasVigenciaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('hipotecas')
        self.other = User.objects.create_user('hipotecas_otra')
        TipoCambio.objects.create(fecha=date(2026, 1, 1), uf=Decimal('40000'), dolar=Decimal('1000'))
        self.dept = Departamento.objects.create(user=self.user, codigo='D1', piso='1', metros_cuadrados=40, valor_compra_uf=1000, valor_actual_uf=1000)
        self.credit = CreditoHipotecario.objects.create(departamento=self.dept, monto_original_uf=800, saldo_actual_uf=800, tasa_anual=4, plazo_anos=20, tipo_tasa='FIJA', cuota_uf=5, cuotas_totales=240)

    def test_hipoteca_manual_se_valora_desde_uf_en_todos_los_reportes_sin_duplicar_saldo(self):
        resumen = resumen_patrimonio(self.user, date(2026, 1, 2))
        self.assertEqual(resumen['pasivos_total_clp'], Decimal('32000000'))
        self.assertEqual(resumen['patrimonio_neto_clp'], Decimal('8000000'))
        self.assertFalse(Pasivo.objects.filter(user=self.user).exists())
        self.client.force_login(self.user)
        for url in ('dashboard', 'patrimonio', 'pasivos'):
            response = self.client.get(reverse(url))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['total_pasivos'], Decimal('32000000'))
        self.assertContains(self.client.get(reverse('pasivos')), 'Editar en Departamentos')

    def test_saldo_bancario_incluido_cero_sustituye_la_proyeccion_manual(self):
        product = Producto.objects.create(user=self.user, banco=Banco.objects.create(nombre='Hip banco'), nombre='Hipoteca', tipo='CREDITO_HIPOTECARIO')
        self.credit.producto = product
        self.credit.save()
        self.assertEqual(resumen_patrimonio(self.user)['pasivos_total_clp'], Decimal('32000000'))
        snapshot = SaldoSnapshot.objects.create(user=self.user, producto=product, fecha=date(2026, 1, 2), saldo_insoluto=Decimal('31000000'))
        self.assertEqual(resumen_patrimonio(self.user)['pasivos_total_clp'], Decimal('31000000'))
        snapshot.saldo_insoluto = Decimal('0')
        snapshot.save()
        self.assertEqual(resumen_patrimonio(self.user)['pasivos_total_clp'], Decimal('0'))

    def test_tasa_historica_se_recalcula_sin_modificar_el_credito(self):
        TipoCambio.objects.create(fecha=date(2026, 2, 1), uf=Decimal('41000'), dolar=Decimal('1000'))
        self.assertEqual(resumen_patrimonio(self.user, date(2026, 1, 15))['pasivos_total_clp'], Decimal('32000000'))
        self.assertEqual(resumen_patrimonio(self.user, date(2026, 2, 15))['pasivos_total_clp'], Decimal('32800000'))
        self.credit.refresh_from_db()
        self.assertEqual(self.credit.saldo_actual_uf, Decimal('800'))

    def test_sin_tasa_uf_se_expone_cobertura_y_conserva_fuente(self):
        TipoCambio.objects.all().delete()
        resumen = resumen_patrimonio(self.user)
        self.assertTrue(resumen['falta_tasa_uf'])
        self.assertTrue(resumen['falta_tasa_usd'])
        self.assertEqual(resumen['pasivos'][0].saldo_uf, Decimal('800'))

    def test_desactivar_inmueble_quita_valor_pero_no_su_deuda_ni_historial(self):
        snapshot = SnapshotPatrimonio.objects.create(user=self.user, fecha=date(2026, 1, 1), activos_total_clp=Decimal('40000000'), activos_total_usd=Decimal('40000'), pasivos_total_clp=Decimal('32000000'), pasivos_total_usd=Decimal('32000'), patrimonio_neto_clp=Decimal('8000000'), patrimonio_neto_usd=Decimal('8000'))
        self.dept.activo = False
        self.dept.save()
        resumen = resumen_patrimonio(self.user)
        self.assertEqual(resumen['activos_total_clp'], Decimal('0'))
        self.assertEqual(resumen['pasivos_total_clp'], Decimal('32000000'))
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.activos_total_clp, Decimal('40000000'))
        self.assertTrue(Departamento.objects.filter(pk=self.dept.pk).exists())
        self.credit.activo = False
        self.credit.save()
        self.assertEqual(resumen_patrimonio(self.user)['pasivos_total_clp'], Decimal('0'))

    def test_hipotecas_y_pasivos_de_otro_usuario_no_se_incluyen(self):
        dept = Departamento.objects.create(user=self.other, codigo='D2', piso='2', metros_cuadrados=40, valor_compra_uf=1000, valor_actual_uf=1000)
        CreditoHipotecario.objects.create(departamento=dept, monto_original_uf=800, saldo_actual_uf=800, tasa_anual=4, plazo_anos=20, tipo_tasa='FIJA', cuota_uf=5, cuotas_totales=240)
        self.assertEqual(resumen_patrimonio(self.user)['pasivos_total_clp'], Decimal('32000000'))
        self.assertEqual(len(resumen_patrimonio(self.user)['pasivos']), 1)

    def test_producto_excluido_no_suma_su_pasivo(self):
        product = Producto.objects.create(user=self.user, banco=Banco.objects.create(nombre='Hip excluida'), nombre='Hipoteca', tipo='CREDITO_HIPOTECARIO', contabilizar=False)
        self.credit.producto = product
        self.credit.save()
        Pasivo.objects.create(user=self.user, producto=product, nombre='No contar', tipo='CREDITO_HIPOTECARIO', monto_original=Decimal('100'), moneda_original='CLP', monto_clp=Decimal('100'))
        self.assertEqual(resumen_patrimonio(self.user)['pasivos_total_clp'], Decimal('0'))

    def test_interfaz_desactiva_reactiva_y_conserva_historia_aislada(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('alternar_departamento', args=[self.dept.pk]), follow=True)
        self.assertEqual(response.status_code, 200)
        self.dept.refresh_from_db()
        self.assertFalse(self.dept.activo)
        self.assertEqual(response.context['total_valor_actual'], 0)
        self.assertContains(response, 'Reactivar')
        active = self.client.get(reverse('departamentos'))
        self.assertFalse(active.context['departamentos'])
        self.assertTrue(CreditoHipotecario.objects.filter(pk=self.credit.pk).exists())
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse('alternar_departamento', args=[self.dept.pk])).status_code, 404)
        self.client.force_login(self.user)
        self.client.post(reverse('alternar_departamento', args=[self.dept.pk]))
        self.assertEqual(resumen_patrimonio(self.user)['activos_total_clp'], Decimal('40000000'))

    def test_snapshot_sin_importes_no_borra_una_deuda_conocida(self):
        product = Producto.objects.create(user=self.user, banco=Banco.objects.create(nombre='Hip parcial'), nombre='Hipoteca', tipo='CREDITO_HIPOTECARIO')
        self.credit.producto = product
        self.credit.save()
        SaldoSnapshot.objects.create(user=self.user, producto=product, fecha=date(2026, 1, 2), saldo_insoluto=Decimal('31000000'))
        SaldoSnapshot.objects.create(user=self.user, producto=product, fecha=date(2026, 1, 3))
        self.assertEqual(resumen_patrimonio(self.user)['pasivos_total_clp'], Decimal('31000000'))

    def test_snapshot_de_una_moneda_no_borra_la_otra_moneda(self):
        product = Producto.objects.create(user=self.user, banco=Banco.objects.create(nombre='Dos monedas'), nombre='Tarjeta', tipo='TDC')
        self.credit.activo = False
        self.credit.save()
        SaldoSnapshot.objects.create(user=self.user, producto=product, fecha=date(2026, 1, 2), deuda_facturada_clp=Decimal('1000'), deuda_usd=Decimal('10'))
        SaldoSnapshot.objects.create(user=self.user, producto=product, fecha=date(2026, 1, 3), deuda_facturada_clp=Decimal('500'))
        self.assertEqual(resumen_patrimonio(self.user)['pasivos_total_clp'], Decimal('10500'))
