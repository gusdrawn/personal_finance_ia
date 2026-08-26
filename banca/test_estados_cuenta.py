import tempfile
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from bank_sync.models import MovimientoNormalizado
from banca.models import ImportacionEstadoCuenta, MovimientoBancario
from banca.services.estados_cuenta import (
    EstadoCuentaParseError,
    EstadoCuentaParseado,
    importar_estado_cuenta,
    normalizar_monto,
    parsear_estado_cuenta_texto,
)


class ParserEstadosCuentaTest(TestCase):
    def test_parsea_itau_tarjeta_periodo_y_movimientos(self):
        texto = """
        Estado de cuenta nacional Itaú
        Nombre del titular: Test
        Nº de tarjeta de crédito: xxxx-xxxx-xxxx-9252
        Fecha de estado de cuenta: 27/07/2026
        Período facturado 24/06/2026 27/07/2026
        Monto total facturado a pagar $ 7.090.094
        Monto mínimo a pagar $ 377.786
        06/07/2026 2026070600000000 Monto Cancelado $ -8.326.949 $ -8.326.949 01/1 $ -8.326.949
        01/07/2026 2026070110481475 Neat Montos Altos San Felipe $ 2.022.240 $ 2.022.240 01/1 $ 2.022.240
        """

        resultado = parsear_estado_cuenta_texto(texto, "itau.pdf")

        self.assertEqual(resultado.banco, "Itaú")
        self.assertEqual(resultado.tarjeta_ultimos4, "9252")
        self.assertEqual(resultado.fecha_estado, date(2026, 7, 27))
        self.assertEqual(resultado.monto_facturado, Decimal("7090094"))
        self.assertEqual(len(resultado.movimientos), 2)
        self.assertEqual(resultado.movimientos[0].monto, Decimal("-8326949"))
        self.assertEqual(resultado.movimientos[1].cuota_total, 1)

    def test_parsea_falabella_y_santander_sin_tomar_cupon_de_pago(self):
        falabella = """
        ESTADO DE CUENTA BancoFalabella
        N° de Contrato: 329610******2754
        Fecha Facturación Estado de Cuenta: 19/08/2026
        Período Facturado 20/07/2026 19/08/2026
        Monto Total Facturado a Pagar 3.774.530
        Las Condes 21/07/2026 Mercadopago *sociedad T 67.000 67.000 01/01 sep-2026 67.000
        """
        santander = """
        ESTADO DE CUENTA EN MONEDA NACIONAL DE TARJETA DE CRÉDITO Santander
        Nº DE TARJETA DE CRÉDITO XXXX XXXX XXXX 6691 WORLDMEMBER VISA
        FECHA ESTADO DE CUENTA 23/07/2026
        PERÍODO FACTURADO 23/06/2026 23/07/2026
        MONTO TOTAL FACTURADO A PAGAR $ 379.830
        MIRANDA 25/06/26MC DONALD'S SAN ANTONIO/A VE 18.633,46 $28.368
        10/08/2026 $ 379.830 10/08/2026 Banco $ 379.830
        """

        falabella_resultado = parsear_estado_cuenta_texto(falabella)
        santander_resultado = parsear_estado_cuenta_texto(santander)

        self.assertEqual(falabella_resultado.tarjeta_ultimos4, "2754")
        self.assertEqual(falabella_resultado.movimientos[0].monto, Decimal("67000"))
        self.assertEqual(santander_resultado.tarjeta_ultimos4, "6691")
        self.assertEqual(len(santander_resultado.movimientos), 1)
        self.assertEqual(santander_resultado.movimientos[0].descripcion, "MC DONALD'S SAN ANTONIO")

    def test_rechaza_banco_no_soportado_y_normaliza_limite_de_monto(self):
        self.assertEqual(normalizar_monto("18.633,46"), Decimal("18633.46"))
        self.assertEqual(normalizar_monto("-8.326.949"), Decimal("-8326949"))
        with self.assertRaises(EstadoCuentaParseError):
            parsear_estado_cuenta_texto("Estado de cuenta de un banco desconocido")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="estados_cuenta_test_"))
class ImportacionEstadosCuentaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="importador", password="x")

    def resultado(self, tarjeta="9252", descripcion="Compra PDF"):
        movimiento = MovimientoNormalizado(
            fecha=date(2026, 7, 1),
            descripcion=descripcion,
            monto=Decimal("12345"),
            moneda="CLP",
            estado="FACTURADO",
            tarjeta_id=tarjeta,
            periodo_factura=date(2026, 7, 27),
        )
        return EstadoCuentaParseado(
            banco="Itaú",
            tarjeta_ultimos4=tarjeta,
            fecha_estado=date(2026, 7, 27),
            periodo_inicio=date(2026, 6, 24),
            periodo_fin=date(2026, 7, 27),
            monto_facturado=Decimal("12345"),
            monto_minimo=Decimal("1000"),
            movimientos=[movimiento],
        )

    @patch("banca.services.estados_cuenta.parsear_estado_cuenta_pdf")
    def test_importa_producto_snapshot_y_movimiento_de_forma_idempotente(self, parsear):
        parsear.return_value = self.resultado()
        archivo = SimpleUploadedFile("itau.pdf", b"contenido de prueba", content_type="application/pdf")

        primera = importar_estado_cuenta(self.user, archivo)
        segunda = importar_estado_cuenta(
            self.user,
            SimpleUploadedFile("itau-duplicado.pdf", b"contenido de prueba", content_type="application/pdf"),
        )

        self.assertEqual(primera.estado, "PROCESADO")
        self.assertEqual(primera.operaciones_nuevas, 1)
        self.assertEqual(segunda.estado, "PROCESADO")
        self.assertTrue(segunda.fue_duplicado)
        self.assertEqual(ImportacionEstadoCuenta.objects.filter(user=self.user).count(), 1)
        self.assertEqual(MovimientoBancario.objects.filter(user=self.user).count(), 1)
        self.assertEqual(primera.movimientos.count(), 1)

    @patch("banca.services.estados_cuenta.parsear_estado_cuenta_pdf")
    def test_misma_operacion_queda_aislada_por_usuario(self, parsear):
        parsear.return_value = self.resultado()
        otro_usuario = User.objects.create_user(username="otro", password="x")
        contenido = b"mismo contenido, cuentas distintas"

        importar_estado_cuenta(
            self.user,
            SimpleUploadedFile("itau.pdf", contenido, content_type="application/pdf"),
        )
        importar_estado_cuenta(
            otro_usuario,
            SimpleUploadedFile("itau.pdf", contenido, content_type="application/pdf"),
        )

        self.assertEqual(MovimientoBancario.objects.filter(user=self.user).count(), 1)
        self.assertEqual(MovimientoBancario.objects.filter(user=otro_usuario).count(), 1)
        self.assertEqual(ImportacionEstadoCuenta.objects.count(), 2)

    def test_pagina_importacion_exige_pdf(self):
        self.client.force_login(self.user)
        respuesta = self.client.post(
            reverse("banca_importar"),
            {"archivos": SimpleUploadedFile("nota.txt", b"no", content_type="text/plain")},
        )
        self.assertRedirects(respuesta, reverse("banca_importar"))
        self.assertContains(self.client.get(reverse("banca_importar")), "Todavía no has importado")

    @patch("banca.services.estados_cuenta.parsear_estado_cuenta_pdf")
    def test_pagina_procesa_varios_archivos_en_un_lote(self, parsear):
        def resultado_por_nombre(_contenido, nombre):
            return self.resultado(
                tarjeta="9252" if "itau" in nombre else "6691",
                descripcion=nombre,
            )

        parsear.side_effect = resultado_por_nombre
        self.client.force_login(self.user)
        respuesta = self.client.post(
            reverse("banca_importar"),
            {
                "archivos": [
                    SimpleUploadedFile("itau.pdf", b"uno", content_type="application/pdf"),
                    SimpleUploadedFile("santander.pdf", b"dos", content_type="application/pdf"),
                ]
            },
        )

        self.assertRedirects(respuesta, reverse("banca_importar"))
        self.assertEqual(ImportacionEstadoCuenta.objects.filter(user=self.user).count(), 2)
        self.assertEqual(MovimientoBancario.objects.filter(user=self.user).count(), 2)
        self.assertContains(self.client.get(reverse("banca_importar")), "itau.pdf")
