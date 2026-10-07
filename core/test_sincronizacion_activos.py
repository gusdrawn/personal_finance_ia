from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from banca.models import CredencialBanco, SyncLog
from bank_sync.models import PosicionNormalizada
from configuracion.models import Banco
from patrimonio.models import Activo, HistorialActivo
from patrimonio.services.sincronizacion import origen_activo


class SincronizacionActivosTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='activo_sync')
        self.otro = User.objects.create_user(username='activo_otro')
        self.banco = Banco.objects.create(nombre='Binance')
        self.credencial = CredencialBanco.objects.create(
            user=self.user, banco=self.banco, conector='binance', tipo_fuente='cripto',
        )
        self.client.force_login(self.user)

    def activo(self, **kwargs):
        datos = dict(user=self.user, nombre='BTC (Binance)', tipo='CRIPTO',
                     monto_original=Decimal('100'), moneda_original='USD',
                     fuente_externa='binance', cuenta_fuente=str(self.credencial.pk),
                     id_fuente='btc')
        datos.update(kwargs)
        return Activo.objects.create(**datos)

    def legado(self):
        activo = self.activo(nombre='BTC Earn (Binance)', fuente_externa='',
                             cuenta_fuente='', id_fuente='')
        inicio = timezone.now() - timedelta(minutes=2)
        instante = inicio + timedelta(seconds=1)
        log = SyncLog.objects.create(user=self.user, banco=self.banco, modo='full',
                                     exito=True, actualizados=1, finalizado=inicio + timedelta(seconds=2))
        SyncLog.objects.filter(pk=log.pk).update(iniciado=inicio)
        log.refresh_from_db()
        Activo.objects.filter(pk=activo.pk).update(updated_at=instante)
        activo.refresh_from_db()
        HistorialActivo.objects.create(activo=activo, fecha=timezone.localdate(instante),
                                       monto_clp=100000, monto_usd=100)
        return activo, log

    def test_ui_distingue_sincronizacion_y_manual(self):
        integrado = self.activo()
        manual = self.activo(nombre='Efectivo', tipo='EFECTIVO', fuente_externa='',
                             cuenta_fuente='', id_fuente='')
        response = self.client.get(reverse('activos'))
        self.assertContains(response, 'Por sincronización · Binance')
        self.assertContains(response, reverse('sincronizar_activo', args=[integrado.pk]))
        self.assertContains(response, 'Editar saldo manual', count=2)
        self.assertNotContains(response, f'openEditActivoModal({integrado.pk},')
        self.assertContains(response, f'openEditActivoModal({manual.pk},')
        self.assertRegex(
            response.content.decode(),
            rf"openEditActivoModal\({manual.pk},[^)]*'USD'",
        )
        self.assertNotContains(response, reverse('borrar_activo', args=[integrado.pk]))

    @patch('banca.services.sync.run_sync')
    def test_post_solo_sincroniza_cuenta_del_activo_y_regresa(self, run):
        activo = self.activo()
        otra = CredencialBanco.objects.create(user=self.otro, banco=self.banco, conector='binance')
        run.return_value = SimpleNamespace(exito=True, iniciado=activo.updated_at)
        response = self.client.post(reverse('sincronizar_activo', args=[activo.pk]),
                                    {'credencial_id': otra.pk, 'conector': 'santander'})
        self.assertRedirects(response, reverse('activos'))
        run.assert_called_once_with(self.credencial, modo='full')
        self.assertTrue(any('completada' in str(m) for m in get_messages(response.wsgi_request)))

    @patch('banca.services.sync.run_sync')
    def test_aislamiento_y_post_obligatorio(self, run):
        ajeno = self.activo(user=self.otro)
        self.assertEqual(self.client.post(reverse('sincronizar_activo', args=[ajeno.pk])).status_code, 404)
        propio = self.activo()
        self.assertEqual(self.client.get(reverse('sincronizar_activo', args=[propio.pk])).status_code, 405)
        run.assert_not_called()

    @patch('banca.services.sync.run_sync')
    def test_credencial_ajena_no_se_resuelve(self, run):
        otra = CredencialBanco.objects.create(user=self.otro, banco=self.banco, conector='binance')
        activo = self.activo(cuenta_fuente=str(otra.pk))
        self.assertIsNone(origen_activo(activo).credencial)
        response = self.client.post(reverse('sincronizar_activo', args=[activo.pk]))
        self.assertRedirects(response, reverse('activos'))
        run.assert_not_called()

    @patch('banca.services.sync.run_sync')
    def test_conexion_inactiva_o_identidad_incompleta_ofrece_configurar(self, run):
        activo = self.activo()
        self.credencial.activo = False
        self.credencial.save()
        response = self.client.get(reverse('activos'))
        self.assertContains(response, 'Configurar fuente')
        self.assertNotContains(response, reverse('sincronizar_activo', args=[activo.pk]))
        self.assertNotContains(response, f'openEditActivoModal({activo.pk},')
        self.client.post(reverse('sincronizar_activo', args=[activo.pk]))
        self.credencial.activo = True
        self.credencial.save()
        Activo.objects.filter(pk=activo.pk).update(cuenta_fuente='')
        activo.refresh_from_db()
        self.assertIsNone(origen_activo(activo).credencial)
        run.assert_not_called()

    @patch('banca.services.sync.run_sync')
    def test_manual_no_dispara_ninguna_fuente(self, run):
        activo = self.activo(fuente_externa='', cuenta_fuente='', id_fuente='')
        self.assertFalse(origen_activo(activo).externo)
        self.client.post(reverse('sincronizar_activo', args=[activo.pk]))
        run.assert_not_called()

    def test_post_manual_no_sobrescribe_ni_borra_integrado(self):
        activo = self.activo()
        self.client.post(reverse('editar_activo', args=[activo.pk]),
                         {'nombre':'Cambio', 'monto_original':'999', 'moneda_original':'CLP'})
        self.client.post(reverse('borrar_activo', args=[activo.pk]))
        activo.refresh_from_db()
        self.assertEqual(activo.nombre, 'BTC (Binance)')
        self.assertEqual(activo.monto_original, Decimal('100'))
        self.assertEqual(activo.moneda_original, 'USD')

    def test_manual_sigue_editable(self):
        activo = self.activo(nombre='Ahorro', fuente_externa='', cuenta_fuente='', id_fuente='')
        self.client.post(reverse('editar_activo', args=[activo.pk]),
                         {'monto_original':'200', 'moneda_original':'CLP'})
        activo.refresh_from_db()
        self.assertEqual(activo.monto_original, Decimal('200'))
        self.assertEqual(activo.moneda_original, 'CLP')

    def test_editar_sin_enviar_moneda_conserva_usd(self):
        activo = self.activo(nombre='Ahorro', fuente_externa='', cuenta_fuente='', id_fuente='')
        response = self.client.post(reverse('editar_activo', args=[activo.pk]),
                                    {'monto_original': '200'})
        self.assertRedirects(response, reverse('activos'))
        activo.refresh_from_db()
        self.assertEqual(activo.monto_original, Decimal('200'))
        self.assertEqual(activo.moneda_original, 'USD')

    def test_legado_corrobora_log_e_historial_sin_escribir_al_consultar(self):
        activo, log = self.legado()
        response = self.client.get(reverse('activos'))
        self.assertContains(response, reverse('sincronizar_activo', args=[activo.pk]))
        activo.refresh_from_db()
        self.assertEqual(activo.fuente_externa, '')
        HistorialActivo.objects.filter(activo=activo).delete()
        self.assertFalse(origen_activo(activo).externo)

    def test_legado_ambiguo_o_log_fallido_no_se_infiere(self):
        activo, log = self.legado()
        log.exito = False
        log.save()
        self.assertFalse(origen_activo(activo).externo)
        log.exito = True
        log.save()
        duplicado = self.activo(nombre=activo.nombre, fuente_externa='', cuenta_fuente='', id_fuente='')
        self.assertTrue(origen_activo(activo).externo)
        self.assertIsNone(origen_activo(activo).credencial)
        duplicado.delete()
        log.user = self.otro
        log.save()
        self.assertFalse(origen_activo(activo).externo)

    @patch('banca.services.sync.run_sync')
    def test_fuente_bancaria_no_se_ejecuta_como_integracion_de_activos(self, run):
        self.credencial.conector = 'santander'
        self.credencial.save()
        activo = self.activo(fuente_externa='santander')
        self.client.post(reverse('sincronizar_activo', args=[activo.pk]))
        run.assert_not_called()
        self.assertIsNone(origen_activo(activo).credencial)

    @patch('banca.services.sync.get_connector')
    def test_sincronizar_legado_actualiza_misma_fila_con_connector_mock(self, factory):
        activo, _ = self.legado()
        connector = Mock()
        connector.get_posiciones.return_value = [PosicionNormalizada(
            simbolo='BTC', nombre=activo.nombre, tipo_activo='CRIPTO', fuente='binance',
            moneda='USD', valor_usd=Decimal('150'),
        )]
        factory.return_value = connector
        with patch.object(CredencialBanco, 'get_api_key', return_value='synthetic'), \
             patch.object(CredencialBanco, 'get_api_secret', return_value='synthetic'):
            response = self.client.post(reverse('sincronizar_activo', args=[activo.pk]))
        self.assertRedirects(response, reverse('activos'))
        activo.refresh_from_db()
        self.assertEqual(Activo.objects.filter(user=self.user).count(), 1)
        self.assertEqual(activo.fuente_externa, 'binance')
        self.assertEqual(activo.cuenta_fuente, str(self.credencial.pk))
        self.assertEqual(activo.monto_original, Decimal('150'))
        connector.authenticate.assert_called_once()

    @patch('banca.services.sync.run_sync')
    def test_error_o_posicion_ausente_informa_y_conserva_saldo(self, run):
        activo = self.activo()
        url = reverse('sincronizar_activo', args=[activo.pk])
        run.return_value = SimpleNamespace(exito=False)
        response = self.client.post(url)
        self.assertTrue(any('No se pudo completar' in str(m) for m in get_messages(response.wsgi_request)))
        run.return_value = SimpleNamespace(exito=True, iniciado=timezone.now())
        response = self.client.post(url)
        self.assertTrue(any('Se conserva el anterior' in str(m) for m in get_messages(response.wsgi_request)))
        run.side_effect = RuntimeError('synthetic outage')
        response = self.client.post(url)
        self.assertTrue(any('No se pudo sincronizar' in str(m) for m in get_messages(response.wsgi_request)))
        activo.refresh_from_db()
        self.assertEqual(activo.monto_original, Decimal('100'))
