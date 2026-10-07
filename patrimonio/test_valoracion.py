from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from configuracion.models import Banco, Producto, TipoCambio
from departamentos.models import Departamento
from patrimonio.models import Activo, Pasivo
from patrimonio.services.valoracion import resumen_patrimonio


class ValoracionPatrimonialTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="valoracion", password="x")
        self.rate = TipoCambio.objects.create(
            fecha=date(2026, 6, 1), uf=Decimal("35000"), dolar=Decimal("1000")
        )

    def test_unifica_activos_pasivos_departamentos_y_moneda_de_reporte(self):
        Activo.objects.create(
            user=self.user, nombre="Broker USD", tipo="BROKERAGE", es_liquido=True,
            monto_clp=Decimal("100000"), monto_usd=Decimal("100"),
            monto_original=Decimal("100"), moneda_original="USD",
        )
        Activo.objects.create(
            user=self.user, nombre="Ahorro CLP", tipo="AHORRO", es_liquido=False,
            monto_clp=Decimal("50000"), monto_usd=Decimal("50"),
            monto_original=Decimal("50000"), moneda_original="CLP",
        )
        departamento = Departamento.objects.create(
            user=self.user, codigo="D01", piso="1", metros_cuadrados=Decimal("40"),
            valor_compra_uf=Decimal("1"), valor_actual_uf=Decimal("2"),
        )
        # Legacy property rows must not be added on top of the canonical department.
        Activo.objects.create(
            user=self.user, nombre="Departamento duplicado", tipo="DEPARTAMENTO",
            departamento=departamento, monto_clp=Decimal("70000"), monto_usd=Decimal("70"),
            monto_original=Decimal("70000"), moneda_original="CLP",
        )
        Pasivo.objects.create(
            user=self.user, nombre="Deuda USD", tipo="PRESTAMO",
            monto_clp=Decimal("10000"), monto_usd=Decimal("10"),
            monto_original=Decimal("10"), moneda_original="USD",
        )
        Pasivo.objects.create(
            user=self.user, nombre="Deuda CLP", tipo="OTRO",
            monto_clp=Decimal("20000"), monto_usd=Decimal("20"),
            monto_original=Decimal("20000"), moneda_original="CLP",
        )

        resumen = resumen_patrimonio(self.user, date(2026, 6, 2))

        self.assertEqual(resumen["activos_total_clp"], Decimal("220000"))
        self.assertEqual(resumen["activos_total_usd"], Decimal("220.00"))
        self.assertEqual(resumen["pasivos_total_clp"], Decimal("30000"))
        self.assertEqual(resumen["pasivos_total_usd"], Decimal("30.00"))
        self.assertEqual(resumen["patrimonio_neto_clp"], Decimal("190000"))
        self.assertEqual(resumen["patrimonio_neto_usd"], Decimal("190.00"))
        self.assertEqual(resumen["activos_liquidos_clp"], Decimal("100000.00"))
        self.assertEqual(resumen["tipo_cambio"], self.rate)

    def test_dashboard_patrimonio_activos_pasivos_y_ratio_comparten_totales(self):
        Activo.objects.create(
            user=self.user, nombre="Caja", tipo="EFECTIVO", es_liquido=True,
            monto_clp=Decimal("100000"), monto_original=Decimal("100000"),
            moneda_original="CLP",
        )
        Pasivo.objects.create(
            user=self.user, nombre="Préstamo", tipo="PRESTAMO",
            monto_clp=Decimal("20000"), monto_original=Decimal("20000"),
            moneda_original="CLP",
        )
        Departamento.objects.create(
            user=self.user, codigo="D02", piso="2", metros_cuadrados=Decimal("40"),
            valor_compra_uf=Decimal("1"), valor_actual_uf=Decimal("2"),
        )
        self.client.force_login(self.user)

        dashboard = self.client.get(reverse("dashboard"))
        patrimonio = self.client.get(reverse("patrimonio"))
        activos = self.client.get(reverse("activos"))
        pasivos = self.client.get(reverse("pasivos"))

        self.assertEqual(dashboard.context["total_activos"], Decimal("170000"))
        self.assertEqual(patrimonio.context["total_activos"], Decimal("170000"))
        self.assertEqual(activos.context["total_invertido"], Decimal("170000"))
        self.assertEqual(patrimonio.context["total_pasivos"], Decimal("20000"))
        self.assertEqual(pasivos.context["total_pasivos"], Decimal("20000"))
        self.assertEqual(pasivos.context["ratio_da"], Decimal("20000") / Decimal("170000") * 100)

    def test_usa_la_tasa_disponible_a_la_fecha_y_expone_falta_de_tasa(self):
        TipoCambio.objects.create(
            fecha=date(2026, 7, 1), uf=Decimal("36000"), dolar=Decimal("1200")
        )
        Activo.objects.create(
            user=self.user, nombre="Ahorro USD", tipo="AHORRO",
            monto_clp=Decimal("90000"), monto_usd=Decimal("100"),
            monto_original=Decimal("100"), moneda_original="USD",
        )

        anterior = resumen_patrimonio(self.user, date(2026, 6, 15))
        posterior = resumen_patrimonio(self.user, date(2026, 7, 15))

        self.assertEqual(anterior["activos_total_clp"], Decimal("100000.00"))
        self.assertEqual(posterior["activos_total_clp"], Decimal("120000.00"))
        self.assertFalse(anterior["falta_tasa_usd"])

        self.rate.delete()
        TipoCambio.objects.filter(fecha=date(2026, 7, 1)).delete()
        sin_tasa = resumen_patrimonio(self.user, date(2026, 7, 15))
        self.assertTrue(sin_tasa["falta_tasa_usd"])
        self.assertEqual(sin_tasa["activos_total_clp"], Decimal("90000"))

    def test_preserva_saldos_ambiguos_y_aisla_otros_usuarios(self):
        Activo.objects.create(
            user=self.user, nombre="Saldo antiguo", tipo="OTRO",
            monto_clp=Decimal("50000"), monto_usd=Decimal("60"),
            monto_original=None, moneda_original=None, requiere_revision_moneda=True,
        )
        other = User.objects.create_user(username="otro_valoracion", password="x")
        Activo.objects.create(
            user=other, nombre="Ajeno", tipo="OTRO", monto_clp=Decimal("999999"),
            monto_original=Decimal("999999"), moneda_original="CLP",
        )

        resumen = resumen_patrimonio(self.user, date(2026, 6, 2))

        self.assertEqual(resumen["activos_total_clp"], Decimal("50000"))
        self.assertEqual(resumen["activos_total_usd"], Decimal("60"))
        self.assertEqual(resumen["filas_por_revisar_moneda"], 1)

    def test_modelos_rechazan_relaciones_de_otro_usuario(self):
        other = User.objects.create_user(username="dueno_relacion", password="x")
        departamento = Departamento.objects.create(
            user=other, codigo="AJENO", piso="1", metros_cuadrados=Decimal("40"),
            valor_compra_uf=Decimal("1"), valor_actual_uf=Decimal("1"),
        )
        producto = Producto.objects.create(
            user=other, banco=Banco.objects.create(nombre="Banco dueño"),
            nombre="Tarjeta ajena", tipo="TDC",
        )

        with self.assertRaises(ValidationError):
            Activo.objects.create(
                user=self.user, nombre="Propiedad ajena", tipo="OTRO",
                departamento=departamento,
            )
        with self.assertRaises(ValidationError):
            Pasivo.objects.create(
                user=self.user, nombre="Producto ajeno", tipo="TDC", producto=producto,
            )
