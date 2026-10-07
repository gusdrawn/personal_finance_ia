from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from banca.models import ImportacionEstadoCuenta, MovimientoBancario
from configuracion.models import Banco, Producto
from gastos.services import resumen_gastos
from pendientes.models import AsignacionPagoPendiente, OcurrenciaPendiente, ReglaPendiente


class ConciliacionAseoTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("aseo_enlace")
        self.other = User.objects.create_user("aseo_enlace_otra")
        bank = Banco.objects.create(nombre="Banco certificado")
        self.product = Producto.objects.create(user=self.user, banco=bank, tipo="CUENTA", nombre="Cuenta")
        self.fecha = date(2026, 8, 10)
        self.document = ImportacionEstadoCuenta.objects.create(user=self.user, nombre_archivo="aseo.pdf", hash_archivo="certificado", tipo_documento="PAGO_ASEO", fecha_pago_real=self.fecha, monto_pagado_clp=Decimal("10000"), monto_facturado=Decimal("10000"))
        self.mov = MovimientoBancario.objects.create(user=self.user, producto=self.product, fecha=self.fecha, descripcion="WEBPAY MUNICIPALIDAD", monto=Decimal("10000"), monto_clp=Decimal("10000"), hash_externo="pago-municipal", estado="FACTURADO")
        for index in (1, 2):
            rule = ReglaPendiente.objects.create(user=self.user, nombre=f"Aseo rol {index}", tipo="PAGO", periodicidad="UNICA", fecha_inicio=self.fecha)
            occurrence = OcurrenciaPendiente.objects.create(regla=rule, fecha_vencimiento=self.fecha, estado="COMPLETADA", fecha_real=self.fecha)
            AsignacionPagoPendiente.objects.create(ocurrencia=occurrence, importacion=self.document, monto_asignado=Decimal("5000"))

    def vincular(self, documento=None, movimiento=None):
        from pendientes.services.pagos import vincular_pago_aseo

        return vincular_pago_aseo(documento or self.document, movimiento or self.mov)

    def test_comprobante_enlazado_no_duplica_y_conserva_desglose(self):
        self.vincular()
        self.vincular()
        for perspectiva in ("caja", "consumo"):
            result = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), perspectiva)
            self.assertEqual(result["totales"]["egresos_clp"], Decimal("10000"))
            lines = [item for group in result["categorias"] for item in group["movimientos"]]
            self.assertEqual({item["descripcion"] for item in lines}, {"Aseo rol 1", "Aseo rol 2"})
        self.assertEqual(self.document.asignaciones_pendientes.count(), 2)
        self.assertEqual(MovimientoBancario.objects.count(), 1)

    def test_documento_sin_pago_bancario_sigue_contabilizado(self):
        self.mov.delete()
        result = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "caja")
        self.assertEqual(result["totales"]["egresos_clp"], Decimal("10000"))

    def test_monto_incompatible_se_rechaza_sin_escribir_enlace(self):
        self.mov.monto = self.mov.monto_clp = Decimal("9000")
        self.mov.save()
        with self.assertRaises(ValueError):
            self.vincular()
        self.document.refresh_from_db()
        self.assertIsNone(self.document.movimiento_pago_id)

    def test_fuente_ajena_se_rechaza(self):
        product = Producto.objects.create(user=self.other, banco=self.product.banco, tipo="CUENTA", nombre="Ajena")
        foreign = MovimientoBancario.objects.create(user=self.other, producto=product, fecha=self.fecha, descripcion="Aseo ajeno", monto=Decimal("10000"), monto_clp=Decimal("10000"), hash_externo="ajeno")
        with self.assertRaises(ValueError):
            self.vincular(movimiento=foreign)

    def test_un_pago_no_respalda_dos_certificados(self):
        self.vincular()
        second = ImportacionEstadoCuenta.objects.create(user=self.user, nombre_archivo="otro.pdf", hash_archivo="otro", tipo_documento="PAGO_ASEO", monto_pagado_clp=Decimal("10000"))
        with self.assertRaises(ValueError):
            self.vincular(documento=second)

    def test_endpoint_y_formulario_aislan_el_usuario(self):
        self.client.force_login(self.user)
        page = self.client.get(reverse("banca_importar"), {"importacion": self.document.pk})
        self.assertContains(page, "Vincular pago bancario")
        result = self.client.post(reverse("banca_vincular_pago_aseo", args=[self.document.pk]), {"movimiento": self.mov.pk})
        self.assertEqual(result.status_code, 302)
        self.client.force_login(self.other)
        denied = self.client.post(reverse("banca_vincular_pago_aseo", args=[self.document.pk]), {"movimiento": self.mov.pk})
        self.assertEqual(denied.status_code, 404)
