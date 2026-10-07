from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from banca.models import ConciliacionEventoExterno, EventoFinancieroExterno, MovimientoBancario
from configuracion.models import Banco, Producto
from patrimonio.models import AbonoCuentaPorCobrar, CuentaPorCobrar, DetalleCuentaPorCobrar
from patrimonio.services.cuentas_por_cobrar import asignar_fuente_a_cuenta


class FuentesSolapadasTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("solapadas")
        bank = Banco.objects.create(nombre="Banco solapadas")
        product = Producto.objects.create(user=self.user, banco=bank, tipo="TDC", nombre="Tarjeta")
        self.mov = MovimientoBancario.objects.create(user=self.user, producto=product, fecha=date(2026, 8, 10), descripcion="NEAT", monto=Decimal("10000"), monto_clp=Decimal("10000"), hash_externo="padre")
        self.account = CuentaPorCobrar.objects.create(user=self.user, nombre="Cuenta terceros")

    def evento(self, amount="10000", referencia="1"):
        event = EventoFinancieroExterno.objects.create(user=self.user, fuente="neat", external_id=referencia, tipo="PAGO", monto_bruto=Decimal(amount), monto_neto=Decimal(amount), fecha_cargo=self.mov.fecha)
        ConciliacionEventoExterno.objects.create(evento=event, movimiento=self.mov, rol="CARGO", monto_asignado=Decimal(amount))
        return event

    def test_asignacion_padre_impide_volver_a_cobrar_su_evento(self):
        event = self.evento()
        asignar_fuente_a_cuenta(self.mov, self.account)
        with self.assertRaises(ValueError):
            asignar_fuente_a_cuenta(event, self.account)
        self.assertEqual(self.account.detalles.count(), 1)
        self.assertEqual(self.account.saldo_pendiente, Decimal("10000"))

    def test_eventos_divididos_se_cobran_y_bloquean_el_padre(self):
        first = self.evento("4000", "a")
        second = self.evento("6000", "b")
        asignar_fuente_a_cuenta(first, self.account)
        asignar_fuente_a_cuenta(second, self.account)
        with self.assertRaises(ValueError):
            asignar_fuente_a_cuenta(self.mov, self.account)
        self.assertEqual(self.account.saldo_pendiente, Decimal("10000"))

    def test_tampoco_duplica_la_obligacion_en_otra_cuenta(self):
        event = self.evento()
        another = CuentaPorCobrar.objects.create(user=self.user, nombre="Otra cuenta")
        asignar_fuente_a_cuenta(event, another)
        with self.assertRaises(ValueError):
            asignar_fuente_a_cuenta(self.mov, self.account)
        self.assertFalse(self.account.detalles.exists())

    def test_liberar_padre_permite_usar_desglose_sin_duplicar(self):
        event = self.evento()
        asignar_fuente_a_cuenta(self.mov, self.account)
        asignar_fuente_a_cuenta(self.mov, None)
        asignar_fuente_a_cuenta(event, self.account)
        asignar_fuente_a_cuenta(event, self.account)
        self.assertEqual(self.account.detalles.count(), 1)

    def test_usuario_ajeno_no_puede_asignar_la_fuente(self):
        other = User.objects.create_user("solapadas_otra")
        account = CuentaPorCobrar.objects.create(user=other, nombre="Ajena")
        with self.assertRaises(ValueError):
            asignar_fuente_a_cuenta(self.mov, account)
        self.assertFalse(account.detalles.exists())

    def test_modelo_impide_el_solapamiento_fuera_del_servicio_de_banca(self):
        event = self.evento()
        asignar_fuente_a_cuenta(self.mov, self.account)
        with self.assertRaises(ValidationError):
            DetalleCuentaPorCobrar.objects.create(cuenta=self.account, descripcion="Detalle duplicado", periodo=date(2026, 8, 1), fecha=self.mov.fecha, monto=Decimal("10000"), evento_externo=event, origen="NEAT")
        self.assertEqual(self.account.detalles.count(), 1)

    def test_interfaz_activos_muestra_error_y_no_crea_otro_cobro(self):
        event = self.evento()
        asignar_fuente_a_cuenta(self.mov, self.account)
        self.client.force_login(self.user)
        response = self.client.post(reverse("agregar_detalle_cuenta_por_cobrar"), {"cuenta_id": self.account.pk, "fuente": f"neat:{event.pk}", "descripcion": "No duplicar", "periodo": "2026-08"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.account.detalles.count(), 1)
        self.assertContains(response, "Este pago ya está asignado")

    def test_edicion_incompatible_conserva_la_linea_y_su_importe(self):
        event = self.evento()
        asignar_fuente_a_cuenta(self.mov, self.account)
        manual = DetalleCuentaPorCobrar.objects.create(cuenta=self.account, descripcion="Independiente", periodo=date(2026, 8, 1), fecha=self.mov.fecha, monto=Decimal("1000"))
        self.client.force_login(self.user)
        response = self.client.post(reverse("editar_detalle_cuenta_por_cobrar", args=[manual.pk]), {"fuente": f"neat:{event.pk}", "descripcion": "No reemplazar", "periodo": "2026-09"}, follow=True)
        self.assertEqual(response.status_code, 200)
        manual.refresh_from_db()
        self.assertEqual(manual.descripcion, "Independiente")
        self.assertEqual(manual.monto, Decimal("1000"))
        self.assertIsNone(manual.evento_externo_id)

    def test_reemplazar_el_padre_en_la_misma_linea_preserva_los_abonos(self):
        event = self.evento()
        detail = asignar_fuente_a_cuenta(self.mov, self.account)
        payment = AbonoCuentaPorCobrar.objects.create(cuenta=self.account, detalle=detail, fecha=self.mov.fecha, monto=Decimal("3000"))
        detail.movimiento = None
        detail.evento_externo = event
        detail.origen = "NEAT"
        detail.save()
        payment.refresh_from_db()
        self.assertEqual(payment.detalle_id, detail.pk)
        self.assertEqual(self.account.saldo_pendiente, Decimal("7000"))
