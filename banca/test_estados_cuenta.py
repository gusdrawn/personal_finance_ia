import tempfile
from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from bank_sync.models import MovimientoNormalizado
from banca.models import (
    CategoriaTematica,
    CredencialBanco,
    ImportacionEstadoCuenta,
    MapeoComercio,
    MovimientoBancario,
    SaldoSnapshot,
)
from banca.services import clasificacion_ia
from banca.services.estados_cuenta import (
    EstadoCuentaParseError,
    EstadoCuentaParseado,
    importar_estado_cuenta,
    normalizar_monto,
    parsear_estado_cuenta_texto,
    reanalizar_importacion,
)
from configuracion.models import Banco, Producto


class ParserEstadosCuentaTest(TestCase):
    def test_parsea_cartolas_de_cuenta_corriente_con_signo_y_saldo(self):
        santander = """
        B A N C O S A N T A N D E R C H I L E
        CUENTA CORRIENTE ML
        PERSONA PRUEBA
        0-000-72-77798-0 008 14728904
        CARTOLA DESDE HASTA PAGINA
        100 31/07/2026 31/08/2026 1 de 1
        05/08Agustinas 0768740690 Transf. INSURTECH SPA 6001444 375.110
        06/08Agustinas Traspaso Internet a Cta. Cte. 2777980 375.110 0
        INFORMACION DE CUENTA CORRIENTE
        SALDO INICIAL DEPOSITOS OTROS ABONOS CHEQUES OTROS CARGOS IMPUESTOS SALDO FINAL
        0 0 375.110 0 375.110 0 0
        """
        itau = """
        Cartola Histórica Cuenta corriente Itaú
        Número de cuenta : 225570967
        Período : 01-Ago-2026 - 31-Ago-2026
        05/08 587373486 Transferencia De Fabiana Aleja $0 $5.000.000 $5.000.000
        06/08 000000000 Pago Tarjeta De Credito $5.000.000 $0 $0
        Saldo final Saldo disponible Saldo promedio
        $0 $0 $0
        """
        falabella = """
        Banco Falabella Cuenta Corriente / Cartola Histórica
        Cuenta 01-999-016366-6 Tipo de Agosto 2026
        Saldo Inicial Saldo Contable Retenciones Saldo Disponible
        $ 2.470.009 $ 3.722.869 $ 0 $ 3.722.869
        31/08/2026 SUCURSAL BTC Transf. para John nelson a $ 50.000 $ 3.722.869
        31/08/2026 SUCURSAL BTC Transf. de FABIANA ALEJANDRA GIL PACHECO $ 3.500.000 $ 3.772.869
        """

        resultados = [
            parsear_estado_cuenta_texto(santander),
            parsear_estado_cuenta_texto(itau),
            parsear_estado_cuenta_texto(falabella),
        ]

        self.assertEqual([r.tipo_producto for r in resultados], ["CUENTA"] * 3)
        self.assertEqual(
            [r.numero_producto for r in resultados],
            ["0-000-72-77798-0", "225570967", "01-999-016366-6"],
        )
        self.assertEqual(resultados[0].saldo_final_clp, Decimal("0"))
        self.assertEqual(resultados[1].movimientos[0].monto, Decimal("-5000000"))
        self.assertEqual(resultados[2].saldo_final_clp, Decimal("3722869"))
        self.assertEqual(
            [m.monto for m in resultados[2].movimientos],
            [Decimal("50000"), Decimal("-3500000")],
        )

    def test_parsea_scotiabank_y_bci_personas_con_saldo_diario(self):
        scotiabank = """
        Consulta Detalle De Cartola Emitida
        Cuenta 0-0098-55883-09
        Sucursal SCOTIA CONNECT 18
        Desde 01/08/2026 Hasta 31/08/2026
        Fecha Emisión 31/08/2026 Saldo inicial 0
        Fecha Descripción Docto. Cargos Abonos Saldo
        06/08/2026 TEF 26228121-3 GUSTAVO DAVID G 000000000 3.000.000 3.000.000
        06/08/2026 TEF 26228121-3 Gustavo BCI 000000000 3.000.000 0
        18/08/2026 COMISION MANTENCION PLAN 000000000 8.532 4.991.468
        26/08/2026 TOMA DEPOSITO A PLAZO SWEB 000000000 15.000.000 4.973.847
        """
        bci = """
        CARTOLA DE CUENTA CORRIENTE CARTOLA N° 8
        Sr(a) GUSTAVO DAVID GUILLEN GUTIERREZ N° CUENTA 98101897 MONEDA PESOS
        PERIODO 31-07-2026 al 31-08-2026
        FECHA SUCURSAL DESCRIPCION DOCUMENTO CARGOS Y ABONOS SALDO DIARIO
        PAG.DIV.035.
        06/08/2026 OF VIRT U 8090578 519.321 -519.321
        CRED.HIPOTEC
        TRANSFER DE G.GUILLEN
        06/08/2026 OF CENTRA 260805214504 3.000.000 2.131.746
        GUT
        TRANSFER A G.GUILLEN
        06/08/2026 OF CENTRA 1445148224 2.131.746 0
        GUTI
        Resumen del Periodo
        Saldo Contable Final del Periodo = 0
        """

        scotia = parsear_estado_cuenta_texto(scotiabank)
        bci_resultado = parsear_estado_cuenta_texto(bci)

        self.assertEqual(scotia.banco, "Scotiabank")
        self.assertEqual(scotia.tipo_producto, "CUENTA")
        self.assertEqual(scotia.numero_producto, "0-0098-55883-09")
        self.assertEqual(scotia.periodo_inicio, date(2026, 8, 1))
        self.assertEqual(scotia.saldo_final_clp, Decimal("4973847"))
        self.assertEqual(
            [mov.monto for mov in scotia.movimientos],
            [Decimal("-3000000"), Decimal("3000000"), Decimal("8532"), Decimal("15000000")],
        )

        self.assertEqual(bci_resultado.banco, "BCI")
        self.assertEqual(bci_resultado.numero_producto, "98101897")
        self.assertEqual(bci_resultado.periodo_fin, date(2026, 8, 31))
        self.assertEqual(bci_resultado.saldo_final_clp, Decimal("0"))
        self.assertEqual(
            [mov.monto for mov in bci_resultado.movimientos],
            [Decimal("519321"), Decimal("-3000000"), Decimal("2131746")],
        )
        self.assertIn("TRANSFER DE G.GUILLEN GUT", bci_resultado.movimientos[1].descripcion)

    def test_parsea_itau_tarjeta_periodo_y_movimientos(self):
        texto = """
        Estado de cuenta nacional Itaú
        Nombre del titular: Test
        Nº de tarjeta de crédito: xxxx-xxxx-xxxx-9252
        Fecha de estado de cuenta: 27/07/2026
        Período facturado 24/06/2026 27/07/2026
        Monto total facturado a pagar $ 7.090.094
        Monto mínimo a pagar $ 377.786
        Pagar hasta Monto total facturado a pagar
        10/08/2026 $ 7.090.094
        Cupo total $ 15.500.000 $ 7.090.094 $ 8.409.906
        Cupo avance en efectivo $ 15.500.000 $ 0 $ 8.409.906
        06/07/2026 2026070600000000 Monto Cancelado $ -8.326.949 $ -8.326.949 01/1 $ -8.326.949
        01/07/2026 2026070110481475 Neat Montos Altos San Felipe $ 2.022.240 $ 2.022.240 01/1 $ 2.022.240
        """

        resultado = parsear_estado_cuenta_texto(texto, "itau.pdf")

        self.assertEqual(resultado.banco, "Itaú")
        self.assertEqual(resultado.tarjeta_ultimos4, "9252")
        self.assertEqual(resultado.fecha_estado, date(2026, 7, 27))
        self.assertEqual(resultado.fecha_vencimiento, date(2026, 8, 10))
        self.assertEqual(resultado.monto_facturado, Decimal("7090094"))
        self.assertEqual(resultado.cupo_total_clp, Decimal("15500000"))
        self.assertEqual(resultado.cupo_utilizado_clp, Decimal("7090094"))
        self.assertEqual(resultado.cupo_disponible_clp, Decimal("8409906"))
        self.assertEqual(resultado.cupo_avance_disponible_clp, Decimal("8409906"))
        self.assertEqual(len(resultado.movimientos), 2)
        self.assertEqual(resultado.movimientos[0].monto, Decimal("-8326949"))
        self.assertEqual(resultado.movimientos[1].cuota_total, 1)

    def test_parsea_falabella_y_santander_sin_tomar_cupon_de_pago(self):
        falabella = """
        ESTADO DE CUENTA BancoFalabella
        N° de Contrato: 329610******2754
        Fecha Facturación Estado de Cuenta: 19/08/2026
        Período Facturado 20/07/2026 19/08/2026
        Pagar Hasta 05/09/2026
        Monto Total Facturado a Pagar 3.774.530
        Cupo Compras* 17.490.000 3.812.010 13.677.990
        Cupo Avance** - - 6.000.000
        Las Condes 21/07/2026 Mercadopago *sociedad T 67.000 67.000 01/01 sep-2026 67.000
        """
        santander = """
        ESTADO DE CUENTA EN MONEDA NACIONAL DE TARJETA DE CRÉDITO Santander
        Nº DE TARJETA DE CRÉDITO XXXX XXXX XXXX 6691 WORLDMEMBER VISA
        FECHA ESTADO DE CUENTA 23/07/2026
        PERÍODO FACTURADO 23/06/2026 23/07/2026
        PAGAR HASTA 10/08/2026
        MONTO TOTAL FACTURADO A PAGAR $ 379.830
        CUPO TOTAL $ 5.000.000 $ 379.830 $ 4.620.170 0,00 %
        CUPO TOTAL AVANCE EN EFECTIVO $0 $0 $0
        MIRANDA 25/06/26MC DONALD'S SAN ANTONIO/A VE 18.633,46 $28.368
        10/08/2026 $ 379.830 10/08/2026 Banco $ 379.830
        """

        falabella_resultado = parsear_estado_cuenta_texto(falabella)
        santander_resultado = parsear_estado_cuenta_texto(santander)

        self.assertEqual(falabella_resultado.tarjeta_ultimos4, "2754")
        self.assertEqual(falabella_resultado.fecha_vencimiento, date(2026, 9, 5))
        self.assertEqual(falabella_resultado.movimientos[0].monto, Decimal("67000"))
        self.assertEqual(falabella_resultado.cupo_total_clp, Decimal("17490000"))
        self.assertEqual(falabella_resultado.cupo_utilizado_clp, Decimal("3812010"))
        self.assertEqual(falabella_resultado.cupo_disponible_clp, Decimal("13677990"))
        self.assertEqual(falabella_resultado.cupo_avance_disponible_clp, Decimal("6000000"))
        self.assertEqual(santander_resultado.tarjeta_ultimos4, "6691")
        self.assertEqual(santander_resultado.fecha_vencimiento, date(2026, 8, 10))
        self.assertEqual(santander_resultado.cupo_total_clp, Decimal("5000000"))
        self.assertEqual(santander_resultado.cupo_utilizado_clp, Decimal("379830"))
        self.assertEqual(santander_resultado.cupo_disponible_clp, Decimal("4620170"))
        self.assertEqual(santander_resultado.cupo_avance_disponible_clp, Decimal("0"))
        self.assertEqual(len(santander_resultado.movimientos), 1)
        self.assertEqual(santander_resultado.movimientos[0].descripcion, "MC DONALD'S SAN ANTONIO")

    def test_parsea_lider_bci_sin_logo_textual_ni_subtotales(self):
        texto = """
        ESTADO DE CUENTA TARJETA DE CRÉDITO
        Número tarjeta XXXXXXXXXXXX0018
        Fecha Estado de Cuenta 26/08/2026
        Cupo Total $ 1.270.000 $ 812.335 $ 457.665
        Cupo Total Avance en Efectivo $ 457.665 0 %
        Cupo Total SuperAvance $ 0 $ 0
        Período Facturado 26/07/2026 25/08/2026
        Pagar Hasta 09/09/2026
        SANTIAGO CL 02/08/2026 PASAJE BUS TIENDA (T) $ 10
        $ 10
        28/07/2026 GLASS LIDER.CL (T) $ -3.520
        $ -3.520
        26/08/2026 -100% DCTO COM ADM|MANTENCION (T) $ 0
        05/08/2026 PAGO $ -454.445
        $ -454.445
        Monto Total Facturado $ 812.335 Opción de Pago Total Facturado
        Monto Mínimo a Pagar $ 22.251
        """

        resultado = parsear_estado_cuenta_texto(texto, "eecc-agosto.pdf")

        self.assertEqual(resultado.banco, "Lider Bci")
        self.assertEqual(resultado.tarjeta_ultimos4, "0018")
        self.assertEqual(resultado.fecha_estado, date(2026, 8, 26))
        self.assertEqual(resultado.periodo_inicio, date(2026, 7, 26))
        self.assertEqual(resultado.periodo_fin, date(2026, 8, 25))
        self.assertEqual(resultado.fecha_vencimiento, date(2026, 9, 9))
        self.assertEqual(resultado.monto_facturado, Decimal("812335"))
        self.assertEqual(resultado.monto_minimo, Decimal("22251"))
        self.assertEqual(resultado.cupo_total_clp, Decimal("1270000"))
        self.assertEqual(resultado.cupo_utilizado_clp, Decimal("812335"))
        self.assertEqual(resultado.cupo_disponible_clp, Decimal("457665"))
        self.assertEqual(resultado.cupo_avance_disponible_clp, Decimal("457665"))
        self.assertEqual(len(resultado.movimientos), 3)
        self.assertEqual(
            [mov.descripcion for mov in resultado.movimientos],
            [
                "PASAJE BUS TIENDA",
                "GLASS LIDER.CL",
                "PAGO",
            ],
        )
        self.assertEqual(resultado.movimientos[-1].monto, Decimal("-454445"))

    def test_parsea_tenpo_con_descripcion_partida_y_pago(self):
        texto = """
        ESTADO DE CUENTA DE TARJETA DE CRÉDITO
        Tenpo Payments S.A
        Nombre del Titular N° Tarjeta de Crédito Digital N° Tarjeta de Crédito Física Fecha Estado de Cuenta
        PERSONA PRUEBA 5155xxxxxxxx2047 5155********7420 20/08/2026
        Cupo Total $9.600.000 $950.695 $8.649.305 0,00%
        Cupo Total
        Avance en $0 $0 $0
        efectivo
        Periodo Facturado 21/07/2026 20/08/2026
        Pagar Hasta 05/09/2026
        null 02/08/2026 PAGO DE TARJETA DE CRÉDITO $-950.695 $-950.695 01/01 $-950.695
        Total de pagos a la cuenta $-950.695
        null 29/07/2026 NEAT ARRIENDO SAN FELIPE CH Digital $400.000 $400.000 00/00 $400.000
        NEAT ARRIENDO ESTACION SAN
        null 29/07/2026 Digital $50.000 $50.000 00/00 $50.000
        FELIPE CH
        null 29/07/2026 NEAT ARRIENDO SAN FELIPE CH Digital $500.695 $500.695 00/00 $500.695
        Total de compras Nacionales $950.695
        Monto Total Facturado (o a Pagar) (A+B+C+D) $950.695
        Monto Mínimo a Pagar $47.534
        """

        resultado = parsear_estado_cuenta_texto(texto, "tenpo.pdf")

        self.assertEqual(resultado.banco, "Tenpo")
        self.assertEqual(resultado.tarjeta_ultimos4, "7420")
        self.assertEqual(resultado.fecha_estado, date(2026, 8, 20))
        self.assertEqual(resultado.periodo_inicio, date(2026, 7, 21))
        self.assertEqual(resultado.periodo_fin, date(2026, 8, 20))
        self.assertEqual(resultado.fecha_vencimiento, date(2026, 9, 5))
        self.assertEqual(resultado.monto_facturado, Decimal("950695"))
        self.assertEqual(resultado.monto_minimo, Decimal("47534"))
        self.assertEqual(resultado.cupo_total_clp, Decimal("9600000"))
        self.assertEqual(resultado.cupo_utilizado_clp, Decimal("950695"))
        self.assertEqual(resultado.cupo_disponible_clp, Decimal("8649305"))
        self.assertEqual(resultado.cupo_avance_disponible_clp, Decimal("0"))
        self.assertEqual(len(resultado.movimientos), 4)
        self.assertEqual(
            resultado.movimientos[2].descripcion,
            "NEAT ARRIENDO ESTACION SAN FELIPE CH",
        )
        self.assertEqual(resultado.movimientos[0].monto, Decimal("-950695"))

    def test_parsea_santander_internacional_en_usd(self):
        texto = """
        ESTADO DE CUENTA INTERNACIONAL DE TARJETA DE CRÉDITO Santander
        Nº DE TARJETA DE CRÉDITO XXXX XXXX XXXX 0776 W. LIMITED VISA
        FECHA ESTADO DE CUENTA 25/08/2026
        CUPO TOTAL US$ 12.000,00 US$ 2.050,50 US$ 9.949,50
        SALDO ANTERIOR FACTURADO US$ 0,00 PERÍODO FACTURADO DESDE 23/07/2026
        ABONO REALIZADO US$ 0,00 PERÍODO FACTURADO HASTA 25/08/2026
        PAGAR HASTA 09/09/2026
        DEUDA TOTAL US$ 2.050,50
        29/07/26 BPAY GLOBAL D MANAMA BH 1.670,00 1.670,00
        30/07/26 BPAY GLOBAL D MANAMA BH 50,00 50,00
        02/08/26 EA *ELECTRONIC ARTS help.ea.com CH 28.305,00 30,50
        10/08/26 BPAY GLOBAL D MANAMA BH 300,00 300,00
        09/09/2026 US$ 2.050,50
        """

        resultado = parsear_estado_cuenta_texto(texto, "santander-usd.pdf")

        self.assertEqual(resultado.banco, "Santander")
        self.assertEqual(resultado.tarjeta_ultimos4, "0776")
        self.assertEqual(resultado.moneda, "USD")
        self.assertEqual(resultado.periodo_inicio, date(2026, 7, 23))
        self.assertEqual(resultado.periodo_fin, date(2026, 8, 25))
        self.assertEqual(resultado.fecha_vencimiento, date(2026, 9, 9))
        self.assertEqual(resultado.monto_facturado, Decimal("2050.50"))
        self.assertEqual(resultado.cupo_total_usd, Decimal("12000"))
        self.assertEqual(resultado.cupo_utilizado_usd, Decimal("2050.50"))
        self.assertEqual(resultado.cupo_disponible_usd, Decimal("9949.50"))
        self.assertIsNone(resultado.cupo_total_clp)
        self.assertEqual(len(resultado.movimientos), 4)
        self.assertTrue(all(mov.moneda == "USD" for mov in resultado.movimientos))
        self.assertEqual(
            sum((mov.monto for mov in resultado.movimientos), Decimal("0")),
            resultado.monto_facturado,
        )

    def test_rechaza_banco_no_soportado_y_normaliza_limite_de_monto(self):
        self.assertEqual(normalizar_monto("18.633,46"), Decimal("18633.46"))
        self.assertEqual(normalizar_monto("-8.326.949"), Decimal("-8326949"))
        with self.assertRaises(EstadoCuentaParseError):
            parsear_estado_cuenta_texto("Estado de cuenta de un banco desconocido")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="estados_cuenta_test_"))
class ImportacionEstadosCuentaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="importador", password="x")

    def resultado(
        self,
        tarjeta="9252",
        descripcion="Compra PDF",
        fecha_estado=date(2026, 7, 27),
        fecha_vencimiento=date(2026, 8, 10),
        cupo_total=Decimal("15500000"),
        cupo_utilizado=Decimal("7090094"),
        cupo_disponible=Decimal("8409906"),
        cupo_avance=Decimal("8409906"),
    ):
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
            fecha_estado=fecha_estado,
            fecha_vencimiento=fecha_vencimiento,
            periodo_inicio=date(2026, 6, 24),
            periodo_fin=fecha_estado,
            monto_facturado=Decimal("12345"),
            monto_minimo=Decimal("1000"),
            movimientos=[movimiento],
            cupo_total_clp=cupo_total,
            cupo_utilizado_clp=cupo_utilizado,
            cupo_disponible_clp=cupo_disponible,
            cupo_avance_disponible_clp=cupo_avance,
        )

    @patch("banca.services.fuentes_externas.conciliar_eventos")
    @patch("banca.services.estados_cuenta.parsear_estado_cuenta_pdf")
    def test_cartola_crea_cuenta_y_snapshot_aislados_por_usuario(self, parsear, conciliar):
        otro = User.objects.create_user(username="otra-cuenta", password="x")
        resultado = self.resultado(tarjeta="7798")
        resultado.tipo_producto = "CUENTA"
        resultado.numero_producto = "0-000-72-77798-0"
        resultado.saldo_final_clp = Decimal("5486826")
        resultado.fecha_vencimiento = None
        parsear.return_value = resultado

        importacion = importar_estado_cuenta(
            self.user,
            SimpleUploadedFile("cartola.pdf", b"cuenta-a", content_type="application/pdf"),
        )
        importar_estado_cuenta(
            otro,
            SimpleUploadedFile("cartola.pdf", b"cuenta-b", content_type="application/pdf"),
        )

        self.assertEqual(importacion.producto.tipo, "CUENTA")
        self.assertEqual(importacion.producto.ultimos4, "7980")
        self.assertEqual(importacion.movimientos.get().producto, importacion.producto)
        self.assertEqual(
            SaldoSnapshot.objects.get(user=self.user).saldo_disponible_clp,
            Decimal("5486826"),
        )
        self.assertEqual(Producto.objects.filter(user=self.user, tipo="CUENTA").count(), 1)
        self.assertEqual(Producto.objects.filter(user=otro, tipo="CUENTA").count(), 1)

    @patch("banca.services.fuentes_externas.conciliar_eventos")
    @patch("banca.services.estados_cuenta.parsear_estado_cuenta_pdf")
    def test_importa_producto_snapshot_y_movimiento_de_forma_idempotente(self, parsear, conciliar):
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
        self.assertEqual(primera.cupo_disponible_clp, Decimal("8409906"))
        snapshot = SaldoSnapshot.objects.get(user=self.user)
        self.assertEqual(snapshot.cupo_total_clp, Decimal("15500000"))
        self.assertEqual(snapshot.cupo_utilizado_clp, Decimal("7090094"))
        self.assertEqual(snapshot.cupo_disponible_clp, Decimal("8409906"))
        self.assertEqual(snapshot.fecha_vencimiento, date(2026, 8, 10))
        conciliar.assert_called_once_with(self.user)

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
        self.assertEqual(SaldoSnapshot.objects.filter(user=self.user).count(), 1)
        self.assertEqual(SaldoSnapshot.objects.filter(user=otro_usuario).count(), 1)

    @patch("banca.services.estados_cuenta.parsear_estado_cuenta_pdf")
    def test_pdf_modificado_crea_importacion_pero_no_duplica_operacion_conocida(self, parsear):
        parsear.return_value = self.resultado()
        primera = importar_estado_cuenta(
            self.user,
            SimpleUploadedFile("julio.pdf", b"version uno", content_type="application/pdf"),
        )
        segunda = importar_estado_cuenta(
            self.user,
            SimpleUploadedFile("julio-corregido.pdf", b"version dos", content_type="application/pdf"),
        )

        self.assertNotEqual(primera.pk, segunda.pk)
        self.assertEqual(ImportacionEstadoCuenta.objects.filter(user=self.user).count(), 2)
        self.assertEqual(MovimientoBancario.objects.filter(user=self.user).count(), 1)
        self.assertEqual(segunda.operaciones_nuevas, 0)
        self.assertEqual(segunda.movimientos.count(), 1)

    @patch("banca.services.estados_cuenta.parsear_estado_cuenta_pdf")
    def test_reanalisis_actualiza_metadatos_y_conserva_decision_manual(self, parsear):
        parsear.return_value = self.resultado()
        importacion = importar_estado_cuenta(
            self.user,
            SimpleUploadedFile("julio.pdf", b"archivo persistido", content_type="application/pdf"),
        )
        movimiento = importacion.movimientos.get()
        categoria = CategoriaTematica.objects.create(user=self.user, nombre="Manual")
        movimiento.categoria = categoria
        movimiento.categoria_es_manual = True
        movimiento.fuente_clasificacion = "MANUAL"
        movimiento.save()
        parsear.return_value = self.resultado(
            cupo_total=Decimal("19000000"),
            cupo_utilizado=Decimal("7000000"),
            cupo_disponible=Decimal("12000000"),
        )

        reanalizar_importacion(self.user, importacion)

        importacion.refresh_from_db()
        movimiento.refresh_from_db()
        self.assertEqual(importacion.cupo_total_clp, Decimal("19000000"))
        self.assertEqual(movimiento.categoria, categoria)
        self.assertTrue(movimiento.categoria_es_manual)

    @patch("banca.services.estados_cuenta.parsear_estado_cuenta_pdf")
    def test_mejora_de_cupo_actualiza_el_vigente_y_conserva_historial(self, parsear):
        parsear.side_effect = [
            self.resultado(),
            self.resultado(
                fecha_estado=date(2026, 8, 27),
                cupo_total=Decimal("18000000"),
                cupo_utilizado=Decimal("8000000"),
                cupo_disponible=Decimal("10000000"),
                cupo_avance=Decimal("7500000"),
            ),
        ]
        importar_estado_cuenta(
            self.user,
            SimpleUploadedFile("julio.pdf", b"julio", content_type="application/pdf"),
        )
        importar_estado_cuenta(
            self.user,
            SimpleUploadedFile("agosto.pdf", b"agosto", content_type="application/pdf"),
        )

        snapshots = SaldoSnapshot.objects.filter(user=self.user).order_by("fecha")
        self.assertEqual(snapshots.count(), 2)
        self.assertEqual(snapshots.last().cupo_total_clp, Decimal("18000000"))
        self.assertEqual(snapshots.last().cupo_disponible_clp, Decimal("10000000"))
        self.assertEqual(snapshots.first().cupo_disponible_clp, Decimal("8409906"))

    @patch("banca.services.estados_cuenta.parsear_estado_cuenta_pdf")
    def test_importacion_prefiere_banco_configurado_por_el_usuario(self, parsear):
        from configuracion.models import Banco

        banco_global = Banco.objects.create(nombre="Itau")
        banco_usuario = Banco.objects.create(nombre="Itaú")
        CredencialBanco.objects.create(
            user=self.user,
            banco=banco_usuario,
            conector="itau",
            tipo_fuente="banco",
        )
        parsear.return_value = self.resultado()

        importacion = importar_estado_cuenta(
            self.user,
            SimpleUploadedFile("itau.pdf", b"preferido", content_type="application/pdf"),
        )

        self.assertEqual(importacion.banco_id, banco_usuario.id)
        self.assertNotEqual(importacion.banco_id, banco_global.id)

    @patch("banca.services.estados_cuenta.parsear_estado_cuenta_pdf")
    def test_lider_bci_reutiliza_producto_existente_sin_duplicarlo(self, parsear):
        banco = Banco.objects.create(nombre="Lider")
        producto = Producto.objects.create(
            user=self.user, banco=banco, nombre="Lider BCI", tipo="TDC", activo=True
        )
        resultado = self.resultado(tarjeta="0018")
        resultado.banco = "Lider Bci"
        parsear.return_value = resultado

        importacion = importar_estado_cuenta(
            self.user,
            SimpleUploadedFile("lider.pdf", b"lider-bci", content_type="application/pdf"),
        )

        self.assertEqual(importacion.banco, banco)
        self.assertEqual(importacion.producto, producto)
        self.assertEqual(Producto.objects.filter(banco=banco, tipo="TDC").count(), 1)
        producto.refresh_from_db()
        self.assertIn("0018", producto.nombre)
        self.assertEqual(importacion.movimientos.get().producto, producto)

    @patch("banca.services.estados_cuenta.parsear_estado_cuenta_pdf")
    def test_tenpo_reutiliza_su_unica_tarjeta_existente(self, parsear):
        banco = Banco.objects.create(nombre="Tenpo")
        producto = Producto.objects.create(
            user=self.user, banco=banco, nombre="Mastercard Black", tipo="TDC", activo=True
        )
        resultado = self.resultado(tarjeta="7420")
        resultado.banco = "Tenpo"
        parsear.return_value = resultado

        importacion = importar_estado_cuenta(
            self.user,
            SimpleUploadedFile("tenpo.pdf", b"tenpo", content_type="application/pdf"),
        )

        producto.refresh_from_db()
        self.assertEqual(importacion.producto, producto)
        self.assertIn("7420", producto.nombre)
        self.assertEqual(importacion.movimientos.get().producto, producto)
        self.assertEqual(Producto.objects.filter(banco=banco, tipo="TDC").count(), 1)

    @patch("banca.services.estados_cuenta.parsear_estado_cuenta_pdf")
    def test_importa_usd_en_snapshot_sin_reemplazar_saldos_clp(self, parsear):
        banco = Banco.objects.create(nombre="Santander")
        producto = Producto.objects.create(
            user=self.user, banco=banco, nombre="Visa ••••0776", tipo="TDC", activo=True, ultimos4="0776"
        )
        SaldoSnapshot.objects.create(
            user=self.user,
            producto=producto,
            fecha=date(2026, 8, 25),
            deuda_facturada_clp=Decimal("7000000"),
            cupo_total_clp=Decimal("17000000"),
            cupo_disponible_clp=Decimal("10000000"),
        )
        movimiento = MovimientoNormalizado(
            fecha=date(2026, 8, 2),
            descripcion="Compra USD",
            monto=Decimal("30.50"),
            moneda="USD",
            estado="FACTURADO",
            tarjeta_id="0776",
            periodo_factura=date(2026, 8, 25),
        )
        parsear.return_value = EstadoCuentaParseado(
            banco="Santander",
            tarjeta_ultimos4="0776",
            fecha_estado=date(2026, 8, 25),
            fecha_vencimiento=date(2026, 9, 9),
            periodo_inicio=date(2026, 7, 23),
            periodo_fin=date(2026, 8, 25),
            monto_facturado=Decimal("2050.50"),
            monto_minimo=None,
            movimientos=[movimiento],
            moneda="USD",
            cupo_total_usd=Decimal("12000"),
            cupo_utilizado_usd=Decimal("2050.50"),
            cupo_disponible_usd=Decimal("9949.50"),
        )

        importacion = importar_estado_cuenta(
            self.user,
            SimpleUploadedFile("santander-usd.pdf", b"usd", content_type="application/pdf"),
        )

        producto.refresh_from_db()
        snapshot = SaldoSnapshot.objects.get(
            user=self.user, producto=producto, fecha=date(2026, 8, 25)
        )
        mov = importacion.movimientos.get()
        self.assertEqual(importacion.moneda, "USD")
        self.assertEqual(importacion.cupo_total_usd, Decimal("12000"))
        self.assertEqual(importacion.cupo_disponible_usd, Decimal("9949.50"))
        self.assertIsNone(importacion.cupo_total_clp)
        self.assertEqual(snapshot.deuda_usd, Decimal("2050.50"))
        self.assertEqual(snapshot.cupo_usd, Decimal("12000"))
        self.assertEqual(snapshot.cupo_usd_disponible, Decimal("9949.50"))
        self.assertEqual(snapshot.deuda_facturada_clp, Decimal("7000000"))
        self.assertEqual(snapshot.cupo_total_clp, Decimal("17000000"))
        self.assertTrue(producto.tiene_cupo_usd)
        self.assertEqual(mov.moneda, "USD")
        self.assertEqual(mov.monto, Decimal("30.50"))
        self.assertEqual(mov.monto_clp, Decimal("0"))
        self.assertTrue(mov.es_cargo)

        self.client.force_login(self.user)
        pagina = self.client.get(
            reverse("banca_importar"), {"importacion": importacion.pk}
        )
        self.assertContains(pagina, "Cupo disponible USD")
        self.assertContains(pagina, "US$30,50")

    @patch(
        "banca.views.clasificar_pendientes",
        return_value={"regla": 0, "ia": 0, "sin_resolver": 0},
    )
    @patch("banca.services.estados_cuenta.parsear_estado_cuenta_pdf")
    def test_subir_pdf_duplicado_aplica_parser_actual_sin_duplicar(
        self, parsear, clasificar
    ):
        contenido = b"mismo-pdf-con-parser-nuevo"
        parsear.side_effect = [
            self.resultado(
                cupo_total=None,
                cupo_utilizado=None,
                cupo_disponible=None,
                cupo_avance=None,
            ),
            self.resultado(
                cupo_total=Decimal("19000000"),
                cupo_utilizado=Decimal("7000000"),
                cupo_disponible=Decimal("12000000"),
                cupo_avance=Decimal("6000000"),
            ),
        ]
        original = importar_estado_cuenta(
            self.user,
            SimpleUploadedFile("agosto.pdf", contenido, content_type="application/pdf"),
        )

        self.client.force_login(self.user)
        respuesta = self.client.post(
            reverse("banca_importar"),
            {
                "archivos": SimpleUploadedFile(
                    "agosto.pdf", contenido, content_type="application/pdf"
                )
            },
            follow=True,
        )

        original.refresh_from_db()
        self.assertContains(respuesta, "se reanalizaron con el lector actual")
        self.assertEqual(ImportacionEstadoCuenta.objects.filter(user=self.user).count(), 1)
        self.assertEqual(MovimientoBancario.objects.filter(user=self.user).count(), 1)
        self.assertEqual(original.cupo_total_clp, Decimal("19000000"))
        self.assertEqual(original.cupo_disponible_clp, Decimal("12000000"))
        clasificar.assert_called_once()

    def test_pagina_importacion_exige_pdf(self):
        self.client.force_login(self.user)
        respuesta = self.client.post(
            reverse("banca_importar"),
            {"archivos": SimpleUploadedFile("nota.txt", b"no", content_type="text/plain")},
        )
        self.assertRedirects(respuesta, reverse("banca_importar"))
        self.assertContains(self.client.get(reverse("banca_importar")), "Todavía no has importado")

    def test_pagina_importacion_muestra_zona_de_arrastre_y_limite(self):
        self.client.force_login(self.user)

        pagina = self.client.get(reverse("banca_importar"))

        self.assertContains(pagina, "data-file-dropzone")
        self.assertContains(pagina, "Arrastra tus PDFs aquí")
        self.assertContains(pagina, "data-file-input")
        self.assertContains(pagina, "data-file-clear")
        self.assertContains(pagina, "file-dropzone.js")
        self.assertContains(pagina, "15728640")

    @patch("banca.services.estados_cuenta.parsear_estado_cuenta_pdf")
    @patch(
        "banca.views.clasificar_pendientes",
        return_value={"regla": 0, "ia": 2, "sin_resolver": 0},
    )
    def test_pagina_procesa_varios_archivos_en_un_lote(self, clasificar, parsear):
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

        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(respuesta["Location"], reverse("banca_importar"))
        self.assertEqual(ImportacionEstadoCuenta.objects.filter(user=self.user).count(), 2)
        self.assertEqual(MovimientoBancario.objects.filter(user=self.user).count(), 2)
        clasificar.assert_called_once()
        pagina = self.client.get(reverse("banca_importar"))
        self.assertContains(pagina, "itau.pdf")
        self.assertContains(pagina, "IA sin API key configurada")
        self.assertContains(pagina, "Clasificación automática: 2 por IA")

    @patch("banca.services.estados_cuenta.parsear_estado_cuenta_pdf")
    def test_usuario_puede_fijar_el_comercio_y_aprenderlo_para_el_futuro(self, parsear):
        parsear.return_value = self.resultado(descripcion="Comercio Variable 123456")
        importacion = importar_estado_cuenta(
            self.user,
            SimpleUploadedFile("comercio.pdf", b"comercio-1", content_type="application/pdf"),
        )
        movimiento = importacion.movimientos.get()
        categoria = CategoriaTematica.objects.create(
            user=self.user, nombre="Comida", esencialidad_defecto="FIJO"
        )
        self.client.force_login(self.user)
        self.client.post(
            reverse("banca_reclasificar", args=[movimiento.pk]),
            {"categoria": categoria.pk, "comercio": "  Comercio Estable 999999  "},
            HTTP_REFERER=reverse("banca_importar"),
        )

        mapeo = MapeoComercio.objects.get(user=self.user)
        self.assertEqual(mapeo.patron, "comercio estable")
        self.assertEqual(mapeo.origen, "MANUAL")

        futuro = MovimientoBancario.objects.create(
            user=self.user,
            producto=movimiento.producto,
            fecha=date(2026, 8, 1),
            descripcion="Comercio Estable 888888",
            monto=Decimal("20000"),
            monto_clp=Decimal("20000"),
            hash_externo="futuro-comercio",
        )
        clasificador = Mock()
        resultado = clasificacion_ia.clasificar_pendientes(
            self.user, ids=[futuro.pk], clasificador=clasificador
        )

        futuro.refresh_from_db()
        self.assertEqual(resultado, {"regla": 1, "ia": 0, "sin_resolver": 0})
        self.assertEqual(futuro.categoria_id, categoria.pk)
        clasificador.clasificar.assert_not_called()


class CuposDashboardTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dashboard-cupos", password="x")
        banco = Banco.objects.create(nombre="Banco Cupos")
        producto = Producto.objects.create(
            user=self.user, banco=banco, nombre="Visa ****1234", tipo="TDC", ultimos4="1234"
        )
        SaldoSnapshot.objects.create(
            user=self.user,
            producto=producto,
            fecha=date(2026, 8, 27),
            cupo_total_clp=Decimal("2000000"),
            cupo_utilizado_clp=Decimal("750000"),
            cupo_disponible_clp=Decimal("1250000"),
            cupo_avance_disponible_clp=Decimal("500000"),
            fecha_vencimiento=date(2026, 8, 10),
            deuda_usd=Decimal("2050.50"),
        )

    def test_dashboard_principal_muestra_resumen_de_cupos(self):
        self.client.force_login(self.user)

        respuesta = self.client.get(reverse("dashboard"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Cupo disponible de tarjetas")
        self.assertEqual(
            respuesta.context["cupos_tdc"]["disponible_clp"], Decimal("1250000")
        )

    def test_dashboard_banca_muestra_cupo_y_subcupo_de_avance(self):
        self.client.force_login(self.user)

        respuesta = self.client.get(reverse("banca_dashboard"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Cupo disponible")
        self.assertContains(respuesta, "Avance:")
        self.assertEqual(
            respuesta.context["cupos_tdc"]["tarjetas"][0]["cupo_avance_disponible_clp"],
            Decimal("500000"),
        )

    def test_calendario_muestra_vencimiento_leido_del_estado(self):
        self.client.force_login(self.user)

        respuesta = self.client.get(
            reverse("calendario"), {"year": 2026, "month": 8}
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Pagar TDC")
        self.assertEqual(
            respuesta.context["vencimientos_tarjetas"][0]["fecha"],
            date(2026, 8, 10),
        )
        self.assertEqual(
            respuesta.context["vencimientos_tarjetas"][0]["monto_usd"],
            Decimal("2050.50"),
        )
