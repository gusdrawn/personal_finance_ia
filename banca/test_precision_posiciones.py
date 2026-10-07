from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from banca.models import CredencialBanco
from banca.services.ingesta_posiciones import ingestar_posiciones
from banca.services.sync import run_sync
from bank_sync.models import PosicionNormalizada
from configuracion.models import Banco
from patrimonio.models import Activo, HistorialActivo


class PrecisionPosicionesTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='precision_sync')
        self.otro = User.objects.create_user(username='precision_otro')

    def posicion(self, **kwargs):
        datos = dict(simbolo='BTC', nombre='BTC Earn (Binance)', tipo_activo='CRIPTO',
                     fuente='binance', cantidad=Decimal('0.000123456789'),
                     precio_unitario=Decimal('63543.123456'), moneda='USD')
        datos.update(kwargs)
        return PosicionNormalizada(**datos)

    def test_sync_binance_guarda_valor_de_alta_precision_sin_validationerror(self):
        credencial = CredencialBanco.objects.create(
            user=self.user, banco=Banco.objects.create(nombre='Binance'), conector='binance',
        )
        connector = Mock()
        connector.get_posiciones.return_value = [self.posicion()]
        with patch('banca.services.sync.get_connector', return_value=connector), \
             patch.object(CredencialBanco, 'get_api_key', return_value='synthetic'), \
             patch.object(CredencialBanco, 'get_api_secret', return_value='synthetic'):
            log = run_sync(credencial)
        self.assertTrue(log.exito, log.mensaje)
        activo = Activo.objects.get(user=self.user)
        self.assertEqual(activo.monto_original, Decimal('7.84'))
        self.assertEqual(activo.moneda_original, 'USD')
        self.assertEqual(activo.monto_usd, Decimal('7.84'))
        self.assertEqual(HistorialActivo.objects.get(activo=activo).monto_usd, Decimal('7.84'))

    def test_actualiza_misma_posicion_y_redondea_medio_centavo_hacia_arriba(self):
        ingestar_posiciones(self.user, [self.posicion(valor_usd=Decimal('100.005'))], cuenta_fuente_id='1')
        activo = Activo.objects.get(user=self.user)
        self.assertEqual(activo.monto_original, Decimal('100.01'))
        resultado = ingestar_posiciones(self.user, [self.posicion(valor_usd=Decimal('101.23456789'))], cuenta_fuente_id='1')
        activo.refresh_from_db()
        self.assertEqual(resultado['actualizados'], 1)
        self.assertEqual(activo.monto_original, Decimal('101.23'))
        self.assertEqual(Activo.objects.filter(user=self.user).count(), 1)
        self.assertEqual(HistorialActivo.objects.filter(activo=activo).count(), 1)

    def test_legado_se_conserva_y_cero_es_un_saldo_valido(self):
        activo = Activo.objects.create(user=self.user, nombre='BTC Earn (Binance)', tipo='CRIPTO')
        ingestar_posiciones(self.user, [self.posicion(valor_usd=Decimal('25.123456'))], cuenta_fuente_id='1')
        activo.refresh_from_db()
        self.assertEqual(activo.monto_original, Decimal('25.12'))
        ingestar_posiciones(self.user, [self.posicion(valor_usd=Decimal('0'))], cuenta_fuente_id='1')
        activo.refresh_from_db()
        self.assertEqual(activo.monto_original, Decimal('0.00'))
        self.assertEqual(Activo.objects.filter(user=self.user).count(), 1)

    def test_importes_explicitos_clp_y_moneda_alternativa_admiten_precision(self):
        ingestar_posiciones(self.user, [self.posicion(moneda='CLP', valor_clp=Decimal('1500.555'))])
        activo = Activo.objects.get(user=self.user)
        self.assertEqual(activo.monto_original, Decimal('1500.56'))
        self.assertEqual(activo.moneda_original, 'CLP')
        ingestar_posiciones(self.user, [self.posicion(simbolo='ETH', nombre='ETH (Binance)',
                            moneda='USD', cantidad=None, precio_unitario=None, valor_clp=Decimal('900.555'))])
        eth = Activo.objects.get(user=self.user, id_fuente='eth')
        self.assertEqual(eth.monto_original, Decimal('900.56'))
        self.assertEqual(eth.moneda_original, 'CLP')

    def test_aislamiento_entre_usuarios(self):
        ingestar_posiciones(self.otro, [self.posicion(valor_usd=Decimal('90.005'))], cuenta_fuente_id='1')
        ajeno = Activo.objects.get(user=self.otro)
        ingestar_posiciones(self.user, [self.posicion(valor_usd=Decimal('20.005'))], cuenta_fuente_id='1')
        ajeno.refresh_from_db()
        self.assertEqual(ajeno.monto_original, Decimal('90.01'))
        self.assertEqual(Activo.objects.get(user=self.user).monto_original, Decimal('20.01'))

    def test_posicion_invalida_revierte_actualizaciones_creaciones_e_historial(self):
        ingestar_posiciones(self.user, [self.posicion(valor_usd=Decimal('10'))], cuenta_fuente_id='1')
        activo = Activo.objects.get(user=self.user)
        historial = HistorialActivo.objects.get(activo=activo)
        fecha_actualizacion = activo.updated_at
        with self.assertRaises(ValidationError):
            ingestar_posiciones(self.user, [
                self.posicion(valor_usd=Decimal('20.005')),
                self.posicion(simbolo='ETH', nombre='ETH (Binance)', valor_usd=Decimal('30.005')),
                self.posicion(simbolo='BAD', nombre='BAD (Binance)', valor_usd=Decimal('10000000000000')),
            ], cuenta_fuente_id='1')
        activo.refresh_from_db()
        historial.refresh_from_db()
        self.assertEqual(activo.monto_original, Decimal('10'))
        self.assertEqual(activo.updated_at, fecha_actualizacion)
        self.assertEqual(historial.monto_usd, Decimal('10'))
        self.assertEqual(Activo.objects.filter(user=self.user).count(), 1)
        self.assertEqual(HistorialActivo.objects.filter(activo__user=self.user).count(), 1)
