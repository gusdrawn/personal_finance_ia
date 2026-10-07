from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from banca.models import CategoriaTematica, MovimientoBancario
from banca.services.transferencias import categoria_transferencia_propia
from configuracion.models import Banco, Producto
from gastos.services import resumen_gastos
from prestamos.models import Prestamo


class CorreccionMovimientosTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="correccion")
        cls.other = User.objects.create_user(username="correccion_ajena")
        banco = Banco.objects.create(nombre="Banco corrección")
        cls.cuenta = Producto.objects.create(user=cls.user, banco=banco, nombre="Cuenta", tipo="CUENTA")
        cls.sueldo = CategoriaTematica.objects.create(user=cls.user, nombre="Sueldos", aplica_a="INGRESO")
        cls.servicios = CategoriaTematica.objects.create(user=cls.user, nombre="Servicios", aplica_a="EGRESO")
        cls.abono = MovimientoBancario.objects.create(
            user=cls.user, producto=cls.cuenta, fecha=date(2026, 8, 24),
            descripcion="Transferencia Fabiana", monto=Decimal("-80000"),
            monto_clp=Decimal("-80000"), categoria=cls.sueldo,
            estado="FACTURADO", hash_externo="fabiana-abono",
        )
        cls.cargo = MovimientoBancario.objects.create(
            user=cls.user, producto=cls.cuenta, fecha=date(2026, 8, 24),
            descripcion="Pago Fabiana", monto=Decimal("12000"), monto_clp=Decimal("12000"),
            categoria=cls.servicios, estado="FACTURADO", hash_externo="fabiana-cargo",
        )
        otra_cuenta = Producto.objects.create(user=cls.other, banco=banco, nombre="Privada", tipo="CUENTA")
        cls.ajeno = MovimientoBancario.objects.create(
            user=cls.other, producto=otra_cuenta, fecha=date(2026, 8, 24),
            descripcion="Fabiana privada", monto=Decimal("-900000"), monto_clp=Decimal("-900000"),
            estado="FACTURADO", hash_externo="fabiana-privada",
        )

    def setUp(self):
        self.client.force_login(self.user)
        self.filtros = {
            "q": "fabiana", "fecha_desde": "2026-08-01", "fecha_hasta": "2026-08-31",
            "banco": "", "producto": "", "categoria": "", "origen": "",
            "estado": "CONFIRMADO", "naturaleza": "",
        }

    def test_busqueda_en_gastos_muestra_abono_clasificado_para_corregirlo(self):
        response = self.client.get(reverse("gastos_movimientos"), self.filtros)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_movimientos"], 2)
        self.assertContains(response, self.abono.descripcion)
        self.assertContains(response, self.cargo.descripcion)
        self.assertNotContains(response, self.ajeno.descripcion)
        formulario = response.content.decode().split(
            f'action="{reverse("banca_reclasificar", args=[self.abono.pk])}"', 1
        )[1].split("</form>", 1)[0]
        self.assertIn(self.sueldo.nombre, formulario)
        self.assertIn("Transferencias entre mis cuentas", formulario)
        self.assertNotIn(self.servicios.nombre, formulario)
        grafica = response.context["categoria_chart"]
        self.assertEqual(len(grafica), 1)
        self.assertEqual(grafica[0]["id"], self.servicios.pk)
        self.assertEqual(Decimal(grafica[0]["total"]), Decimal("12000"))

    def test_filtro_explicito_de_cargos_o_abonos(self):
        for direccion, visible, oculto in (
            ("INGRESO", self.abono, self.cargo), ("EGRESO", self.cargo, self.abono),
        ):
            with self.subTest(direccion=direccion):
                response = self.client.get(reverse("gastos_movimientos"), {
                    **self.filtros, "direccion": direccion,
                })
                self.assertEqual(response.context["total_movimientos"], 1)
                self.assertEqual(response.context["f_direccion"], direccion)
                self.assertContains(response, visible.descripcion)
                self.assertNotContains(response, oculto.descripcion)
                self.assertNotContains(response, self.ajeno.descripcion)

    def test_corregir_sueldo_a_transferencia_guarda_y_conserva_busqueda(self):
        categoria = categoria_transferencia_propia(self.user)
        retorno = reverse("gastos_movimientos") + "?q=fabiana&fecha_desde=2026-08-01&fecha_hasta=2026-08-31&estado=CONFIRMADO#clasificacion-movimientos"
        response = self.client.post(reverse("banca_reclasificar", args=[self.abono.pk]), {
            "categoria": categoria.pk,
        }, HTTP_REFERER=retorno)
        self.assertRedirects(response, retorno)
        self.abono.refresh_from_db()
        self.assertEqual(self.abono.naturaleza, "TRANSFERENCIA")
        self.assertEqual(self.abono.categoria, categoria)
        self.assertTrue(self.abono.categoria_es_manual)
        self.assertTrue(self.abono.naturaleza_es_manual)
        resumen = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "consumo")
        self.assertEqual(resumen["totales"]["ingresos_clp"], Decimal("0"))
        self.assertContains(self.client.get(reverse("gastos_movimientos"), self.filtros), self.abono.descripcion)

    def test_corregir_transferencia_sin_categoria_a_ingreso_real_confirma_revision(self):
        self.abono.categoria = None
        self.abono.naturaleza = "TRANSFERENCIA"
        self.abono.por_revisar = True
        self.abono.save(update_fields=["categoria", "naturaleza", "por_revisar"])
        self.client.post(reverse("banca_reclasificar", args=[self.abono.pk]), {"categoria": self.sueldo.pk})
        self.abono.refresh_from_db()
        self.assertEqual(self.abono.naturaleza, "OPERACIONAL")
        self.assertFalse(self.abono.por_revisar)
        self.assertTrue(self.abono.contabiliza)
        resumen = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "consumo")
        self.assertEqual(resumen["totales"]["ingresos_clp"], Decimal("80000"))

    def test_corregir_abono_a_bicicleta_especifica(self):
        bicicleta = Prestamo.objects.create(user=self.user, nombre="Bicicleta prueba", tipo="TERCEROS", monto_total=100000)
        response = self.client.post(reverse("banca_asignar_prestamo", args=[self.abono.pk]), {"prestamo": bicicleta.pk})
        self.assertEqual(response.status_code, 302)
        self.abono.refresh_from_db()
        self.assertEqual(self.abono.prestamo, bicicleta)
        self.assertEqual(self.abono.categoria.nombre, "Bicicletas")
        self.assertEqual(self.abono.naturaleza, "TRANSFERENCIA")
        self.assertFalse(self.abono.contabiliza)

    def test_reclasificar_pago_de_tarjeta_conserva_financiamiento_sin_doble_gasto(self):
        tarjeta = CategoriaTematica.objects.create(
            user=self.user, nombre="Financiamiento / Pago de TDC", aplica_a="EGRESO",
        )
        self.cargo.naturaleza = "TRANSFERENCIA"
        self.cargo.categoria = tarjeta
        self.cargo.save(update_fields=["naturaleza", "categoria"])
        self.client.post(reverse("banca_reclasificar", args=[self.cargo.pk]), {"categoria": tarjeta.pk})
        self.cargo.refresh_from_db()
        self.assertEqual(self.cargo.naturaleza, "TRANSFERENCIA")
        self.assertFalse(self.cargo.contabiliza)

    def test_rechaza_categoria_del_sentido_incorrecto_sin_alterar_abono(self):
        response = self.client.post(reverse("banca_reclasificar", args=[self.abono.pk]), {
            "categoria": self.servicios.pk,
        }, follow=True)
        self.assertContains(response, "La categoría seleccionada no corresponde a este cargo o abono.")
        self.abono.refresh_from_db()
        self.assertEqual(self.abono.categoria, self.sueldo)
        self.assertEqual(self.abono.naturaleza, "OPERACIONAL")

    def test_correccion_no_permite_categoria_o_movimiento_ajeno(self):
        categoria = CategoriaTematica.objects.create(user=self.other, nombre="Categoría privada", aplica_a="INGRESO")
        self.assertEqual(self.client.post(reverse("banca_reclasificar", args=[self.abono.pk]), {
            "categoria": categoria.pk,
        }).status_code, 404)
        self.assertEqual(self.client.post(reverse("banca_reclasificar", args=[self.ajeno.pk]), {
            "categoria": self.sueldo.pk,
        }).status_code, 404)
        self.abono.refresh_from_db()
        self.assertEqual(self.abono.categoria, self.sueldo)
