from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from banca.models import CategoriaTematica, MovimientoBancario
from configuracion.models import Banco, Producto
from gastos.services import resumen_gastos


class MovimientosViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="movimientos_user", password="x")
        self.other = User.objects.create_user(username="movimientos_other", password="x")
        self.bank = Banco.objects.create(nombre="Banco Movimientos")
        self.product = Producto.objects.create(
            user=self.user, banco=self.bank, nombre="Visa ••••4321", tipo="TDC", ultimos4="4321"
        )
        self.food = CategoriaTematica.objects.create(
            user=self.user, nombre="Supermercado", aplica_a="EGRESO", origen="USUARIO"
        )
        self.transport = CategoriaTematica.objects.create(
            user=self.user, nombre="Transporte", aplica_a="EGRESO", origen="USUARIO"
        )

    def movement(self, description, category, hash_value, amount="12000"):
        return MovimientoBancario.objects.create(
            user=self.user,
            producto=self.product,
            fecha=date(2026, 8, 10),
            descripcion=description,
            monto=Decimal(amount),
            monto_clp=Decimal(amount),
            categoria=category,
            estado="FACTURADO",
            origen="PDF",
            hash_externo=hash_value,
        )

    def test_busqueda_muestra_solo_la_coincidencia_y_grafica(self):
        self.movement("Supermercado Central", self.food, "search-food")
        self.movement("Metro Santiago", self.transport, "search-transport")
        self.client.force_login(self.user)

        response = self.client.get(reverse("banca_movimientos"), {"q": "Central"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Supermercado Central")
        self.assertNotContains(response, "Metro Santiago")
        self.assertContains(response, "movimientos-categorias-chart")
        self.assertContains(response, "Haz clic en una categoría para filtrar")
        self.assertContains(response, "filtros-movimientos').submit()")

    def test_filtro_categoria_restringe_tabla_sin_perder_contexto_de_grafica(self):
        self.movement("Supermercado Central", self.food, "filter-food")
        self.movement("Metro Santiago", self.transport, "filter-transport")
        MovimientoBancario.objects.create(
            user=self.user,
            producto=self.product,
            fecha=date(2026, 8, 11),
            descripcion="Reembolso Supermercado",
            monto=Decimal("-2000"),
            monto_clp=Decimal("-2000"),
            categoria=self.food,
            estado="FACTURADO",
            origen="PDF",
            hash_externo="filter-refund",
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("banca_movimientos"), {"categoria": str(self.food.pk)}
        )

        self.assertContains(response, "Supermercado Central")
        self.assertNotContains(response, "Metro Santiago")
        self.assertContains(response, '"nombre": "Transporte"')
        chart_totals = {row["nombre"]: row["total"] for row in response.context["categoria_chart"]}
        self.assertEqual(chart_totals["Supermercado"], "10000")

    def test_movimientos_de_otro_usuario_no_aparecen_en_busqueda_ni_grafica(self):
        MovimientoBancario.objects.create(
            user=self.other,
            producto=Producto.objects.create(
                user=self.other, banco=self.bank, nombre="Otra tarjeta", tipo="TDC"
            ),
            fecha=date(2026, 8, 10),
            descripcion="Movimiento privado",
            monto=Decimal("99999"),
            monto_clp=Decimal("99999"),
            categoria=CategoriaTematica.objects.create(
                user=self.other, nombre="Privada", aplica_a="EGRESO"
            ),
            estado="FACTURADO",
            origen="PDF",
            hash_externo="other-user-movement",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("banca_movimientos"), {"q": "Movimiento"})

        self.assertNotContains(response, "Movimiento privado")
        self.assertNotContains(response, "Privada")

    def test_pago_de_tdc_con_otra_tdc_es_financiamiento_no_consumo(self):
        movimiento = self.movement(
            "Recaudacion Hiper Lina Linares",
            self.food,
            "pago-tdc-con-tdc",
            amount="454445",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("banca_asignar_prestamo", args=[movimiento.pk]),
            {"prestamo": "transferencia"},
        )

        self.assertEqual(response.status_code, 302)
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.naturaleza, "TRANSFERENCIA")
        self.assertEqual(movimiento.categoria.nombre, "Financiamiento / Pago de TDC")
        self.assertTrue(movimiento.naturaleza_es_manual)
        self.assertTrue(movimiento.categoria_es_manual)
        consumo = resumen_gastos(
            self.user, date(2026, 8, 1), date(2026, 8, 1), "consumo"
        )
        self.assertEqual(consumo["totales"]["egresos_clp"], Decimal("0"))

        pantalla = self.client.get(reverse("banca_movimientos"))
        self.assertContains(pantalla, "Pago de otra TDC")
