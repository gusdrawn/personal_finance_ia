from datetime import date
from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from bank_sync.models import MovimientoNormalizado
from banca.models import ImportacionEstadoCuenta, MovimientoBancario, SaldoSnapshot
from banca.services.estados_cuenta import EstadoCuentaParseado, importar_estado_cuenta
from configuracion.models import Banco, Producto
from patrimonio.models import Pasivo


class AtomicidadPDFTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('atomicidad_pdf')
        self.other = User.objects.create_user('atomicidad_pdf_otra')
        self.bank = Banco.objects.create(nombre='Itaú')
        self.product = Producto.objects.create(user=self.user, banco=self.bank, nombre='TDC', tipo='TDC', ultimos4='9252')
        self.media = TemporaryDirectory(prefix='pdf_atomicidad_')
        self.addCleanup(self.media.cleanup)
        config = override_settings(MEDIA_ROOT=self.media.name)
        config.enable()
        self.addCleanup(config.disable)
        self.parsed = EstadoCuentaParseado(
            banco='Itaú', tarjeta_ultimos4='9252', fecha_estado=date(2026, 7, 31),
            fecha_vencimiento=date(2026, 8, 10), periodo_inicio=date(2026, 7, 1),
            periodo_fin=date(2026, 7, 31), monto_facturado=Decimal('1000'), monto_minimo=Decimal('100'),
            movimientos=[MovimientoNormalizado(fecha=date(2026, 7, 10), descripcion='Compra PDF', monto=Decimal('1000'), moneda='CLP', estado='FACTURADO', tarjeta_id='9252')],
            cupo_total_clp=Decimal('10000'),
        )

    def importar(self, user=None, content=b'pdf-sintetico'):
        with patch('banca.services.estados_cuenta.parsear_estado_cuenta_pdf', return_value=self.parsed):
            return importar_estado_cuenta(user or self.user, SimpleUploadedFile('estado.pdf', content))

    def test_fallo_posterior_revierte_movimientos_snapshot_y_vigencia(self):
        previous = ImportacionEstadoCuenta.objects.create(user=self.user, producto=self.product, banco=self.bank, nombre_archivo='previo.pdf', hash_archivo='previo', estado='PROCESADO', fecha_estado=self.parsed.fecha_estado, fecha_vencimiento=self.parsed.fecha_vencimiento, monto_facturado=Decimal('500'))
        with patch('banca.services.estados_cuenta._guardar_snapshot', side_effect=RuntimeError('Fallo sintético posterior')):
            failed = self.importar()
        previous.refresh_from_db()
        self.assertTrue(previous.vigente)
        self.assertEqual(failed.estado, 'ERROR')
        self.assertIn('Fallo sintético', failed.mensaje)
        self.assertEqual(failed.operaciones_nuevas, 0)
        self.assertFalse(failed.movimientos.exists())
        self.assertFalse(MovimientoBancario.objects.filter(user=self.user).exists())
        self.assertFalse(SaldoSnapshot.objects.filter(user=self.user).exists())

    def test_reintentar_error_reutiliza_documento_y_exito_es_idempotente(self):
        with patch('banca.services.estados_cuenta._guardar_snapshot', side_effect=RuntimeError('Fallo')):
            failed = self.importar()
        success = self.importar()
        self.assertEqual(success.pk, failed.pk)
        self.assertFalse(success.fue_duplicado)
        self.assertEqual(success.estado, 'PROCESADO')
        self.assertEqual(success.movimientos.count(), 1)
        duplicate = self.importar()
        self.assertTrue(duplicate.fue_duplicado)
        self.assertEqual(MovimientoBancario.objects.filter(user=self.user).count(), 1)
        self.assertEqual(ImportacionEstadoCuenta.objects.filter(user=self.user).count(), 1)

    def test_fallo_despues_de_snapshot_revierte_pasivos_y_actualizacion_de_movimientos(self):
        existing = MovimientoBancario.objects.create(user=self.user, producto=self.product, fecha=date(2026, 7, 10), descripcion='Compra PDF', monto=Decimal('1000'), monto_clp=Decimal('1000'), estado='NO_FACTURADO', hash_externo='previo')
        with patch('banca.services.fuentes_externas.conciliar_eventos', side_effect=RuntimeError('Fallo en conciliación')):
            failed = self.importar()
        existing.refresh_from_db()
        self.assertEqual(failed.estado, 'ERROR')
        self.assertEqual(existing.estado, 'NO_FACTURADO')
        self.assertEqual(existing.hash_externo, 'previo')
        self.assertFalse(existing.importaciones.exists())
        self.assertFalse(Pasivo.objects.filter(user=self.user).exists())
        self.assertFalse(SaldoSnapshot.objects.filter(user=self.user).exists())

    def test_archivo_identico_de_otro_usuario_no_se_confunde_con_el_error(self):
        with patch('banca.services.estados_cuenta._guardar_snapshot', side_effect=RuntimeError('Fallo')):
            failed = self.importar()
        success = self.importar(self.other)
        self.assertEqual(success.estado, 'PROCESADO')
        self.assertNotEqual(success.pk, failed.pk)
        self.assertEqual(MovimientoBancario.objects.get(user=self.other).producto.user_id, self.other.pk)
        self.assertFalse(MovimientoBancario.objects.filter(user=self.user).exists())
