from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse

from banca.models import MovimientoBancario
from configuracion.models import Banco, Producto
from departamentos.models import Departamento

from .models import AsignacionPagoPendiente, OcurrenciaPendiente, ReglaPendiente
from .services.calendario import materializar_ocurrencias
from .services.generador import asegurar_reglas_automaticas
from .services.pagos import resolver_ocurrencia
from .services.resumen import resumen_propiedad


class PendientesServiceTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="pendientes", password="secret")
        self.other = User.objects.create_user(username="pendientes_other", password="secret")
        self.depto = Departamento.objects.create(
            user=self.user,
            codigo="E107",
            piso="10",
            metros_cuadrados=Decimal("55.5"),
            valor_compra_uf=Decimal("3000"),
            valor_actual_uf=Decimal("3300"),
            comuna="Concepción",
            rol_sii="006810315",
        )
        self.regla = ReglaPendiente.objects.create(
            user=self.user,
            nombre="Aseo E107",
            tipo="PAGO",
            departamento=self.depto,
            periodicidad="TRIMESTRAL",
            fecha_inicio=date(2026, 4, 30),
            dia_vencimiento=30,
            monto_estimado=Decimal("22969"),
        )

    def test_aseo_trimestral_y_contribuciones_editables(self):
        fechas = [o.fecha_vencimiento for o in materializar_ocurrencias(self.regla, hoy=date(2026, 4, 1))]
        self.assertIn(date(2026, 4, 30), fechas)
        self.assertIn(date(2026, 7, 30), fechas)
        self.assertEqual(len(fechas), len(set(fechas)))
        materializar_ocurrencias(self.regla, hoy=date(2026, 4, 1))
        self.assertEqual(self.regla.ocurrencias.count(), len(fechas))

    def test_pago_manual_es_unico_y_actualiza_resumen(self):
        ocurrencia = OcurrenciaPendiente.objects.create(
            regla=self.regla,
            fecha_vencimiento=date(2026, 4, 30),
            monto_esperado=Decimal("22969"),
        )
        resolver_ocurrencia(ocurrencia, monto=Decimal("22969"), fecha_real=date(2026, 9, 14))
        resolver_ocurrencia(ocurrencia, monto=Decimal("22969"), fecha_real=date(2026, 9, 14))
        self.assertEqual(AsignacionPagoPendiente.objects.filter(ocurrencia=ocurrencia).count(), 1)
        self.assertEqual(ocurrencia.regla.departamento.movimientos_manuales.count(), 1)
        resumen = resumen_propiedad(self.depto, hoy=date(2026, 9, 15))
        self.assertEqual(resumen["real"], Decimal("22969"))

    def test_un_movimiento_bancario_puede_repartirse_sin_crear_otro(self):
        banco = Banco.objects.create(nombre="Banco Aseo")
        producto = Producto.objects.create(user=self.user, banco=banco, nombre="Cuenta corriente", tipo="CUENTA")
        movimiento = MovimientoBancario.objects.create(
            user=self.user,
            producto=producto,
            fecha=date(2026, 9, 14),
            descripcion="Pago Webpay Aseo agrupado",
            monto=Decimal("172861"),
            monto_clp=Decimal("172861"),
            hash_externo="webpay-aseo-172861",
        )
        second = ReglaPendiente.objects.create(
            user=self.user,
            nombre="Aseo E205",
            tipo="PAGO",
            departamento=Departamento.objects.create(
                user=self.user, codigo="E205", piso="20", metros_cuadrados=Decimal("55"),
                valor_compra_uf=Decimal("3000"), valor_actual_uf=Decimal("3300"),
            ),
            periodicidad="UNICA",
            fecha_inicio=date(2026, 9, 14),
            monto_estimado=Decimal("22969"),
        )
        one = OcurrenciaPendiente.objects.create(regla=self.regla, fecha_vencimiento=date(2026, 9, 14), monto_esperado=Decimal("22969"))
        two = OcurrenciaPendiente.objects.create(regla=second, fecha_vencimiento=date(2026, 9, 14), monto_esperado=Decimal("22969"))
        resolver_ocurrencia(one, movimiento_bancario=movimiento, monto=Decimal("22969"))
        resolver_ocurrencia(two, movimiento_bancario=movimiento, monto=Decimal("22969"))
        self.assertEqual(MovimientoBancario.objects.filter(user=self.user).count(), 1)
        self.assertEqual(AsignacionPagoPendiente.objects.filter(movimiento_bancario=movimiento).count(), 2)

        tercera = OcurrenciaPendiente.objects.create(
            regla=self.regla, fecha_vencimiento=date(2026, 10, 14),
            monto_esperado=Decimal("130000"),
        )
        with self.assertRaises(ValueError):
            resolver_ocurrencia(tercera, movimiento_bancario=movimiento, monto=Decimal("130000"))
        self.assertEqual(
            AsignacionPagoPendiente.objects.filter(movimiento_bancario=movimiento).aggregate(
                total=Sum("monto_asignado")
            )["total"],
            Decimal("45938"),
        )

    def test_asignacion_rechaza_fuente_de_otro_usuario(self):
        banco = Banco.objects.create(nombre="Banco ajeno")
        producto = Producto.objects.create(
            user=self.other, banco=banco, nombre="Cuenta ajena", tipo="CUENTA"
        )
        movimiento = MovimientoBancario.objects.create(
            user=self.other, producto=producto, fecha=date(2026, 9, 14),
            descripcion="Pago ajeno", monto=Decimal("1000"), monto_clp=Decimal("1000"),
            hash_externo="pago-ajeno",
        )
        ocurrencia = OcurrenciaPendiente.objects.create(
            regla=self.regla, fecha_vencimiento=date(2026, 9, 14),
            monto_esperado=Decimal("500"),
        )

        with self.assertRaises(ValidationError):
            AsignacionPagoPendiente.objects.create(
                ocurrencia=ocurrencia, movimiento_bancario=movimiento,
                monto_asignado=Decimal("500"),
            )

    def test_sin_cartola_conserva_el_estado_del_modelo(self):
        regla = ReglaPendiente.objects.create(
            user=self.user, nombre="Cartola mensual", tipo="DOCUMENTO",
            periodicidad="MENSUAL", fecha_inicio=date(2026, 9, 1),
            contabiliza_gasto=False,
        )
        ocurrencia = OcurrenciaPendiente.objects.create(
            regla=regla, fecha_vencimiento=date(2026, 9, 30),
        )

        resolver_ocurrencia(ocurrencia, estado="SIN_CARTOLA", nota="El banco no publicó cartola.")

        ocurrencia.refresh_from_db()
        self.assertEqual(ocurrencia.estado, "SIN_CARTOLA")
        self.assertEqual(ocurrencia.nota_resolucion, "El banco no publicó cartola.")
        self.assertEqual(AsignacionPagoPendiente.objects.filter(ocurrencia=ocurrencia).count(), 0)

    def test_reglas_automaticas_idempotentes_y_aisladas(self):
        banco = Banco.objects.create(nombre="Banco Tareas")
        Producto.objects.create(user=self.user, banco=banco, nombre="TDC", tipo="TDC", ultimos4="1234")
        Producto.objects.create(user=self.other, banco=banco, nombre="TDC otro", tipo="TDC", ultimos4="5678")
        asegurar_reglas_automaticas(self.user)
        asegurar_reglas_automaticas(self.user)
        self.assertEqual(ReglaPendiente.objects.filter(user=self.user, automatica=True).count(), 1)
        self.assertEqual(ReglaPendiente.objects.filter(user=self.other).count(), 0)

    def test_sin_movimientos_silencia_ciclo_y_programa_el_siguiente(self):
        stale_rule = ReglaPendiente.objects.create(
            user=self.user,
            nombre="Cartola cuenta",
            tipo="DOCUMENTO",
            periodicidad="DESDE_ULTIMO_REGISTRO",
            fecha_inicio=date.today(),
            contabiliza_gasto=False,
        )
        ocurrencia = OcurrenciaPendiente.objects.create(
            regla=stale_rule,
            fecha_vencimiento=date.today(),
        )
        resolver_ocurrencia(ocurrencia, estado="SIN_MOVIMIENTOS")
        from pendientes.services.calendario import materializar_ocurrencias

        materializar_ocurrencias(stale_rule, hoy=date.today())
        self.assertFalse(stale_rule.ocurrencias.filter(estado="PENDIENTE", fecha_vencimiento=date.today()).exists())
        self.assertTrue(stale_rule.ocurrencias.filter(estado="PENDIENTE", fecha_vencimiento=date.today() + timedelta(days=30)).exists())

    def test_configuracion_manual_de_propiedad_crea_regla_y_ocurrencia(self):
        self.client.force_login(self.user)
        respuesta = self.client.post(
            reverse("pendientes_configurar_propiedad_manual"),
            {
                "departamento_id": self.depto.pk,
                "obligacion": "ASEO",
                "periodicidad": "TRIMESTRAL",
                "fecha_inicio": "2026-09-30",
                "dia_vencimiento": "30",
                "monto_estimado": "22969",
                "canal_cobro": "DIRECTO",
            },
        )

        self.assertRedirects(respuesta, reverse("dashboard_pendientes"))
        regla = ReglaPendiente.objects.get(user=self.user, clave_fuente=f"aseo:{self.depto.pk}")
        self.assertEqual(regla.monto_estimado, Decimal("22969"))
        self.assertTrue(regla.ocurrencias.filter(fecha_vencimiento=date(2026, 9, 30), monto_esperado=Decimal("22969")).exists())


class PendientesIsolationTest(TestCase):
    def test_panel_y_api_no_exponen_otro_usuario(self):
        one = User.objects.create_user(username="api_one", password="secret")
        two = User.objects.create_user(username="api_two", password="secret")
        ReglaPendiente.objects.create(user=one, nombre="Solo uno", fecha_inicio=date.today())
        ReglaPendiente.objects.create(user=two, nombre="Solo dos", fecha_inicio=date.today())
        self.client.force_login(one)
        self.assertContains(self.client.get(reverse("dashboard_pendientes")), "Solo uno")
        self.assertNotContains(self.client.get(reverse("dashboard_pendientes")), "Solo dos")
        self.assertContains(self.client.get(reverse("dashboard_pendientes")), "Configurar obligación de propiedad")
        response = self.client.get("/api/pendientes/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["regla_nombre"] for row in response.json()], ["Solo uno"])

    def test_api_rechaza_propiedad_de_otro_usuario(self):
        one = User.objects.create_user(username="api_owner", password="secret")
        two = User.objects.create_user(username="api_foreign", password="secret")
        foreign = Departamento.objects.create(
            user=two, codigo="FOREIGN", piso="1", metros_cuadrados=Decimal("40"),
            valor_compra_uf=Decimal("1000"), valor_actual_uf=Decimal("1100"),
        )
        self.client.force_login(one)
        response = self.client.post("/api/pendientes/", data={"nombre": "No", "departamento": foreign.id, "fecha_inicio": str(date.today())}, content_type="application/json")
        self.assertEqual(response.status_code, 400)
