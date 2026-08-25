"""Pruebas de integración del snapshot patrimonial manual."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from configuracion.models import TipoCambio
from departamentos.models import Departamento
from patrimonio.models import Activo, Pasivo, HistorialActivo, SnapshotPatrimonio


class SnapshotPatrimonioViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="snapshot_test", password="x")
        self.client.force_login(self.user)
        TipoCambio.objects.create(
            fecha=date.today(),
            fuente="mindicador.cl",
            uf=Decimal("39000"),
            dolar=Decimal("950"),
        )

    def test_snapshot_incluye_activos_liquidos_propiedades_y_pasivos(self):
        Activo.objects.create(
            user=self.user,
            nombre="Caja",
            tipo="EFECTIVO",
            monto_clp=Decimal("1000000"),
            monto_usd=Decimal("1052.63"),
            es_liquido=True,
        )
        Departamento.objects.create(
            user=self.user,
            codigo="E107",
            piso="10",
            metros_cuadrados=Decimal("50"),
            valor_compra_uf=Decimal("2800"),
            valor_actual_uf=Decimal("100"),
        )
        Pasivo.objects.create(
            user=self.user,
            nombre="TDC",
            tipo="TDC",
            monto_clp=Decimal("500000"),
        )

        response = self.client.post(reverse("tomar_snapshot"))

        self.assertEqual(response.status_code, 302)
        snapshot = SnapshotPatrimonio.objects.get(user=self.user, fecha=date.today())
        self.assertEqual(snapshot.activos_total_clp, Decimal("4900000"))
        self.assertEqual(snapshot.pasivos_total_clp, Decimal("500000"))
        self.assertEqual(snapshot.patrimonio_neto_clp, Decimal("4400000"))
        self.assertEqual(snapshot.activos_liquidos_clp, Decimal("1000000"))
        self.assertEqual(HistorialActivo.objects.filter(activo__user=self.user).count(), 1)
