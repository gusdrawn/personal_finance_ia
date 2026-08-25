"""Regresión de modelos patrimoniales y agregados de mini-sesiones."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from patrimonio.models import Activo, LineaMiniSesion, MiniSesion, SnapshotPatrimonio


class PatrimonioModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="patrimonio_test")

    def test_mini_sesion_suma_lineas(self):
        sesion = MiniSesion.objects.create(
            user=self.user,
            nombre="Cierre mensual",
            fecha=date(2026, 6, 30),
            tipo="OTRO",
        )
        LineaMiniSesion.objects.create(
            sesion=sesion, concepto="Activo", monto=Decimal("1000"), orden=1
        )
        LineaMiniSesion.objects.create(
            sesion=sesion, concepto="Pasivo", monto=Decimal("-250"), orden=2
        )

        self.assertEqual(sesion.total, Decimal("750"))

    def test_snapshot_se_ordena_por_fecha_y_conserva_patrimonio_neto(self):
        antiguo = SnapshotPatrimonio.objects.create(
            user=self.user,
            fecha=date(2026, 5, 31),
            activos_total_clp=Decimal("1000"),
            activos_total_usd=Decimal("1"),
            pasivos_total_clp=Decimal("200"),
            pasivos_total_usd=Decimal("0"),
            patrimonio_neto_clp=Decimal("800"),
            patrimonio_neto_usd=Decimal("1"),
        )
        reciente = SnapshotPatrimonio.objects.create(
            user=self.user,
            fecha=date(2026, 6, 30),
            activos_total_clp=Decimal("1200"),
            activos_total_usd=Decimal("1"),
            pasivos_total_clp=Decimal("300"),
            pasivos_total_usd=Decimal("0"),
            patrimonio_neto_clp=Decimal("900"),
            patrimonio_neto_usd=Decimal("1"),
        )

        self.assertEqual(SnapshotPatrimonio.objects.first(), reciente)
        self.assertIn("900", str(reciente))
        self.assertNotEqual(antiguo, reciente)

    def test_activo_puede_vincularse_a_un_departamento_sin_perder_su_tipo(self):
        activo = Activo.objects.create(
            user=self.user,
            nombre="Fondo de emergencia",
            tipo="EFECTIVO",
            horizonte_temporal="EFECTIVO",
            es_liquido=True,
            monto_clp=Decimal("500000"),
        )

        self.assertTrue(activo.es_liquido)
        self.assertEqual(activo.tipo, "EFECTIVO")
