"""Tests del core: cálculo de vencimientos próximos para recordatorios del Calendario."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from core.services.recordatorios import (
    proxima_ocurrencia_gasto,
    proximo_dia_cobro,
    vencimientos_proximos,
)
from gastos.models import CategoriaIngreso, GastoProgramado


class ProximaOcurrenciaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="rec_test")

    def _gp(self, frecuencia, fecha_inicio):
        return GastoProgramado(
            user=self.user,
            nombre="Seguro",
            monto=Decimal("30000"),
            fecha_inicio=fecha_inicio,
            frecuencia=frecuencia,
        )

    def test_mensual_ocurrencia_este_mes(self):
        g = self._gp("MENSUAL", date(2026, 1, 10))
        # hoy 5 de junio -> próxima el 10 de junio
        self.assertEqual(
            proxima_ocurrencia_gasto(g, date(2026, 6, 5)), date(2026, 6, 10)
        )

    def test_mensual_ya_paso_este_mes_va_al_siguiente(self):
        g = self._gp("MENSUAL", date(2026, 1, 10))
        # hoy 15 de junio -> ya pasó el 10, próxima 10 de julio
        self.assertEqual(
            proxima_ocurrencia_gasto(g, date(2026, 6, 15)), date(2026, 7, 10)
        )

    def test_trimestral_respeta_periodo(self):
        g = self._gp("TRIMESTRAL", date(2026, 1, 20))
        # ene, abr, jul, oct... hoy 1 de mayo -> próxima 20 de julio
        self.assertEqual(
            proxima_ocurrencia_gasto(g, date(2026, 5, 1)), date(2026, 7, 20)
        )

    def test_inicio_futuro(self):
        g = self._gp("MENSUAL", date(2026, 12, 1))
        self.assertEqual(
            proxima_ocurrencia_gasto(g, date(2026, 6, 1)), date(2026, 12, 1)
        )

    def test_dia_cobro_recorta_fin_de_mes(self):
        # día 31 en febrero -> se recorta al último día
        self.assertEqual(proximo_dia_cobro(31, date(2026, 2, 1)), date(2026, 2, 28))


class VencimientosProximosTest(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="venc_test")

    def test_filtra_ventana_y_ordena(self):
        hoy = date(2026, 6, 10)
        # dentro de ventana (3 días): GastoProgramado el 12
        GastoProgramado.objects.create(
            user=self.user,
            nombre="Netflix",
            monto=Decimal("9990"),
            fecha_inicio=date(2026, 5, 12),
            frecuencia="MENSUAL",
        )
        # fuera de ventana: el 25
        GastoProgramado.objects.create(
            user=self.user,
            nombre="Seguro",
            monto=Decimal("50000"),
            fecha_inicio=date(2026, 5, 25),
            frecuencia="MENSUAL",
        )
        # CategoriaIngreso con dia_cobro 11 -> dentro de ventana
        CategoriaIngreso.objects.create(
            user=self.user, nombre="Luz", tipo="GASTO_FIJO", dia_cobro=11
        )

        items = vencimientos_proximos(self.user, dias=3, hoy=hoy)
        nombres = [i["nombre"] for i in items]
        self.assertEqual(nombres, ["Luz", "Netflix"])  # ordenado por fecha (11, 12)
        self.assertNotIn("Seguro", nombres)
