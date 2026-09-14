"""Regresión de cálculos financieros del portafolio inmobiliario."""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from configuracion.models import Banco, Producto
from departamentos.models import CreditoHipotecario, Departamento


class DepartamentoModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="depto_test")

    def crear_departamento(self, **extra):
        defaults = {
            "user": self.user,
            "codigo": "E107",
            "piso": "10",
            "metros_cuadrados": Decimal("55.5"),
            "valor_compra_uf": Decimal("3000"),
            "valor_actual_uf": Decimal("3300"),
        }
        defaults.update(extra)
        return Departamento.objects.create(**defaults)

    def test_ganancia_de_capital_y_porcentaje(self):
        departamento = self.crear_departamento()

        self.assertEqual(departamento.ganancia_capital_uf, Decimal("300"))
        self.assertEqual(departamento.porcentaje_ganancia_capital, Decimal("10"))

    def test_progreso_pago_es_cero_antes_de_comenzar_y_cien_al_terminar(self):
        fijo = date(2026, 8, 24)
        futuro = self.crear_departamento(
            codigo="FUT",
            fecha_inicio=date(2026, 8, 25),
            fecha_ultima_cuota=date(2027, 8, 25),
        )
        terminado = self.crear_departamento(
            codigo="FIN",
            fecha_inicio=date(2024, 8, 24),
            fecha_ultima_cuota=date(2025, 8, 24),
        )
        with patch("departamentos.models.date") as date_cls:
            date_cls.today.return_value = fijo
            self.assertEqual(futuro.progreso_pago, 0.0)
            self.assertEqual(terminado.progreso_pago, 100.0)

    def test_progreso_pago_usa_plazo_si_no_hay_fecha_final(self):
        departamento = self.crear_departamento(
            fecha_inicio=date(2025, 8, 24), plazo_anos=2
        )
        with patch("departamentos.models.date") as date_cls:
            date_cls.today.return_value = date(2026, 8, 24)
            self.assertEqual(departamento.progreso_pago, 50.0)

    def test_credito_calcula_amortizacion_y_proyeccion(self):
        departamento = self.crear_departamento()
        credito = CreditoHipotecario.objects.create(
            departamento=departamento,
            monto_original_uf=Decimal("1200"),
            tasa_anual=Decimal("12"),
            plazo_anos=10,
            tipo_tasa="FIJA",
            cuota_uf=Decimal("20"),
            saldo_actual_uf=Decimal("1000"),
            cuotas_totales=120,
        )

        self.assertEqual(credito.calcular_amortizacion_mensual(), Decimal("10"))
        proyeccion = credito.proyectar_amortizacion(meses=2)
        self.assertEqual(proyeccion[0]["interes_uf"], Decimal("10"))
        self.assertEqual(proyeccion[0]["saldo_uf"], Decimal("990"))
        self.assertEqual(proyeccion[1]["saldo_uf"], Decimal("979.9"))
        self.assertEqual(credito.cuotas_restantes, 120)

    def test_banco_display_prefiere_producto_y_admite_fk_legacy(self):
        banco_producto = Banco.objects.create(nombre="Banco Producto")
        producto = Producto.objects.create(
            user=self.user, banco=banco_producto, nombre="Hipoteca", tipo="CREDITO_HIPOTECARIO"
        )
        depto_producto = self.crear_departamento(codigo="PROD")
        con_producto = CreditoHipotecario.objects.create(
            departamento=depto_producto,
            producto=producto,
            monto_original_uf=Decimal("100"),
            tasa_anual=Decimal("5"),
            plazo_anos=10,
            tipo_tasa="FIJA",
            cuota_uf=Decimal("2"),
            saldo_actual_uf=Decimal("90"),
            cuotas_totales=120,
        )
        banco_legacy = Banco.objects.create(nombre="Banco Legacy")
        depto_legacy = self.crear_departamento(codigo="LEG")
        con_legacy = CreditoHipotecario.objects.create(
            departamento=depto_legacy,
            banco=banco_legacy,
            monto_original_uf=Decimal("100"),
            tasa_anual=Decimal("5"),
            plazo_anos=10,
            tipo_tasa="FIJA",
            cuota_uf=Decimal("2"),
            saldo_actual_uf=Decimal("90"),
            cuotas_totales=120,
        )

        self.assertEqual(con_producto.banco_display, banco_producto)
        self.assertEqual(con_legacy.banco_display, banco_legacy)
