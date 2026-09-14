"""Tests de configuracion: parseo de indicadores (UF/USD) y modelo TipoCambio."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from banca.models import MovimientoBancario
from configuracion.models import Banco, Producto, TipoCambio
from configuracion.services.productos import vincular_producto


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class FetchMindicadorTest(TestCase):
    """La descarga de indicadores no debe tocar la red en tests: mockeamos requests.get."""

    def test_parsea_y_upsertea_tasas(self):
        payload = {"uf": {"valor": 39123.45}, "dolar": {"valor": 951.2}}
        with patch(
            "configuracion.utils.requests.get", return_value=_FakeResponse(payload)
        ):
            from configuracion.utils import fetch_mindicador_rates

            tc, ok = fetch_mindicador_rates()

        self.assertTrue(ok)
        self.assertEqual(tc.uf, Decimal("39123.45"))
        self.assertEqual(tc.dolar, Decimal("951.2"))
        self.assertEqual(tc.fuente, "mindicador.cl")
        self.assertEqual(tc.fecha, date.today())

    def test_falla_de_red_no_propaga(self):
        with patch("configuracion.utils.requests.get", side_effect=Exception("boom")):
            from configuracion.utils import fetch_mindicador_rates

            tc, ok = fetch_mindicador_rates()
        self.assertIsNone(tc)
        self.assertFalse(ok)


class ActualizarTasasTaskTest(TestCase):
    """La tarea Celery de indicadores envuelve el fetch y devuelve el resumen."""

    def test_task_actualiza_tasas(self):
        payload = {"uf": {"valor": 39500.0}, "dolar": {"valor": 948.0}}
        with patch(
            "configuracion.utils.requests.get", return_value=_FakeResponse(payload)
        ):
            from configuracion.tasks import actualizar_tasas_diario

            res = actualizar_tasas_diario()

        self.assertTrue(res["ok"])
        self.assertEqual(Decimal(res["uf"]), Decimal("39500"))
        tc = TipoCambio.objects.get(fuente="mindicador.cl")
        self.assertEqual(tc.dolar, Decimal("948"))


class TipoCambioModelTest(TestCase):
    def test_orden_por_fecha_desc(self):
        TipoCambio.objects.create(
            fecha=date(2026, 1, 1),
            fuente="test",
            uf=Decimal("38000"),
            dolar=Decimal("900"),
        )
        reciente = TipoCambio.objects.create(
            fecha=date(2026, 6, 1),
            fuente="test",
            uf=Decimal("39000"),
            dolar=Decimal("950"),
        )
        # Meta.ordering = ['-fecha'] -> el primero es el más reciente
        self.assertEqual(TipoCambio.objects.first(), reciente)


class VinculacionProductoTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("producto_user")
        self.other = User.objects.create_user("producto_other")
        self.bank = Banco.objects.create(nombre="Banco Vinculación")
        self.old = Producto.objects.create(
            user=self.user, banco=self.bank, nombre="Visa antigua", tipo="TDC",
            activo=False, requiere_revision=True,
        )
        self.canonical = Producto.objects.create(
            user=self.user, banco=self.bank, nombre="Visa importada", tipo="TDC",
            ultimos4="1234", origen="IMPORTACION", requiere_revision=True,
        )

    def test_vincula_y_traslada_movimientos(self):
        movement = MovimientoBancario.objects.create(
            user=self.user, producto=self.old, fecha=date(2026, 8, 1), descripcion="Compra",
            monto=Decimal("100"), monto_clp=Decimal("100"), hash_externo="producto-mov",
        )
        vincular_producto(self.user, self.old, self.canonical)
        movement.refresh_from_db()
        self.old.refresh_from_db()
        self.assertEqual(movement.producto, self.canonical)
        self.assertEqual(self.old.reemplazado_por, self.canonical)
        self.assertFalse(self.old.activo)
        self.assertFalse(self.canonical.requiere_revision)

    def test_no_permita_cruzar_usuarios(self):
        foreign = Producto.objects.create(
            user=self.other, banco=self.bank, nombre="Ajena", tipo="TDC"
        )
        with self.assertRaises(ValueError):
            vincular_producto(self.user, self.old, foreign)
