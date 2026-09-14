from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from departamentos.models import Departamento
from core.templatetags.core_extras import tiempo_desde
from patrimonio.models import Activo, Pasivo


class PatrimonioItemUpdatedAtViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="patrimonio_view", password="secret")
        self.other_user = User.objects.create_user(
            username="patrimonio_view_other", password="secret"
        )
        self.client.force_login(self.user)

    def test_activos_muestra_ultima_actualizacion_y_respeta_aislamiento(self):
        activo = Activo.objects.create(
            user=self.user,
            nombre="Fondo de emergencia",
            tipo="EFECTIVO",
            monto_clp=Decimal("500000"),
        )
        departamento = Departamento.objects.create(
            user=self.user,
            codigo="E101",
            piso="10",
            metros_cuadrados=Decimal("50"),
            valor_compra_uf=Decimal("3000"),
            valor_actual_uf=Decimal("3200"),
        )
        hace_30_dias = timezone.now() - timedelta(days=30)
        Activo.objects.filter(pk=activo.pk).update(updated_at=hace_30_dias)
        Departamento.objects.filter(pk=departamento.pk).update(updated_at=hace_30_dias)
        activo.refresh_from_db()
        departamento.refresh_from_db()
        Activo.objects.create(
            user=self.other_user,
            nombre="Activo de otro usuario",
            tipo="EFECTIVO",
        )

        response = self.client.get(reverse("activos"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fondo de emergencia")
        self.assertContains(
            response,
            "Actualizado hace 30 días",
        )
        self.assertContains(response, "Depto E101")
        self.assertContains(
            response,
            "Actualizado hace 30 días",
        )
        self.assertNotContains(response, "Activo de otro usuario")

    def test_pasivos_muestra_ultima_actualizacion_y_respeta_aislamiento(self):
        pasivo = Pasivo.objects.create(
            user=self.user,
            nombre="Crédito personal",
            tipo="PRESTAMO",
            monto_clp=Decimal("250000"),
        )
        Pasivo.objects.filter(pk=pasivo.pk).update(
            updated_at=timezone.now() - timedelta(days=30)
        )
        Pasivo.objects.create(
            user=self.other_user,
            nombre="Pasivo de otro usuario",
            tipo="OTRO",
        )

        response = self.client.get(reverse("pasivos"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Crédito personal")
        self.assertContains(
            response,
            "Actualizado hace 30 días",
        )
        self.assertNotContains(response, "Pasivo de otro usuario")


class TiempoDesdeFilterTest(SimpleTestCase):
    def test_formatea_unidades_relativas_en_espanol(self):
        ahora = timezone.now()
        casos = (
            (timedelta(seconds=10), "hace unos segundos"),
            (timedelta(minutes=1), "hace 1 minuto"),
            (timedelta(hours=2), "hace 2 horas"),
            (timedelta(days=30), "hace 30 días"),
            (timedelta(days=31), "hace 1 mes"),
        )

        for antiguedad, esperado in casos:
            with self.subTest(antiguedad=antiguedad):
                with patch(
                    "core.templatetags.core_extras.timezone.now", return_value=ahora
                ):
                    self.assertEqual(tiempo_desde(ahora - antiguedad), esperado)
