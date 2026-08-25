"""Tests de gastos: cálculo de ahorro mensual de gastos programados (usado en el Calendario)."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from gastos.models import GastoProgramado


class AhorroMensualTest(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="gasto_test")

    def _gp(self, frecuencia, monto="1200"):
        return GastoProgramado(
            user=self.user,
            nombre="Seguro",
            monto=Decimal(monto),
            fecha_inicio=date(2026, 1, 1),
            frecuencia=frecuencia,
        )

    def test_ahorro_mensual_por_frecuencia(self):
        casos = {
            "MENSUAL": Decimal("1200"),
            "BIMESTRAL": Decimal("600"),
            "TRIMESTRAL": Decimal("400"),
            "SEMESTRAL": Decimal("200"),
            "ANUAL": Decimal("100"),
        }
        for frecuencia, esperado in casos.items():
            with self.subTest(frecuencia=frecuencia):
                self.assertEqual(self._gp(frecuencia).ahorro_mensual, esperado)
