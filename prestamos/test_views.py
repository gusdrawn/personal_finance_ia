"""Pruebas de regresión para la integración de bicicletas de terceros."""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from patrimonio.models import Activo
from prestamos.models import Prestamo


class PrestamoTercerosViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="prestamos_test", password="x")
        self.client.force_login(self.user)

    def test_crear_tercero_crea_activo_sin_compensacion_mensual(self):
        response = self.client.post(
            reverse("crear_prestamo"),
            {
                "nombre": "Bicicleta Ana",
                "tipo": "TERCEROS",
                "monto_total": "150000",
                "costo_mensual_porcentaje": "2",
                "year": "2026",
                "month": "6",
            },
        )

        self.assertEqual(response.status_code, 302)
        activo = Activo.objects.get(
            user=self.user, nombre="Bicicleta - Bicicleta Ana"
        )
        self.assertEqual(activo.tipo, "PRESTAMO_DADO")
        self.assertFalse(activo.es_liquido)
        self.assertEqual(activo.monto_clp, Decimal("150000"))

    def test_editar_tercero_sincroniza_monto_y_no_rompe_decimal(self):
        self.client.post(
            reverse("crear_prestamo"),
            {
                "nombre": "Bicicleta Ana",
                "tipo": "TERCEROS",
                "monto_total": "150000",
                "year": "2026",
                "month": "6",
            },
        )
        prestamo = Prestamo.objects.get(user=self.user)

        response = self.client.post(
            reverse("editar_prestamo", args=[prestamo.id]),
            {
                "nombre": "Bicicleta Ana actualizada",
                "tipo": "TERCEROS",
                "monto_total": "175000",
                "costo_mensual_porcentaje": "2",
                "activo": "on",
                "year": "2026",
                "month": "7",
            },
        )

        self.assertEqual(response.status_code, 302)
        activo = Activo.objects.get(
            user=self.user, nombre="Bicicleta - Bicicleta Ana actualizada"
        )
        self.assertEqual(activo.monto_clp, Decimal("175000"))

    def test_index_muestra_ingreso_por_bicicleta_y_no_cuenta_inactivas(self):
        Prestamo.objects.create(
            user=self.user,
            nombre="Bicicleta Ana",
            tipo="TERCEROS",
            monto_total=Decimal("150000"),
            costo_mensual_porcentaje=Decimal("2"),
            activo=True,
        )
        Prestamo.objects.create(
            user=self.user,
            nombre="Bicicleta Bruno",
            tipo="TERCEROS",
            monto_total=Decimal("80000"),
            costo_mensual_porcentaje=Decimal("1.5"),
            activo=False,
        )
        Prestamo.objects.create(
            user=self.user,
            nombre="Bicicleta Propia",
            tipo="PERSONAL",
            monto_total=Decimal("200000"),
            costo_mensual_porcentaje=Decimal("1"),
            activo=True,
        )
        otro = User.objects.create_user(username="otro_usuario", password="x")
        Prestamo.objects.create(
            user=otro,
            nombre="Bicicleta Ajena",
            tipo="TERCEROS",
            monto_total=Decimal("999999"),
            costo_mensual_porcentaje=Decimal("10"),
            activo=True,
        )

        response = self.client.get(reverse("prestamos_index"))

        prestamos = {p.nombre: p for p in response.context["prestamos"]}
        self.assertEqual(prestamos["Bicicleta Ana"].monto_mensual, Decimal("3000"))
        self.assertEqual(prestamos["Bicicleta Bruno"].monto_mensual, Decimal("0"))
        self.assertEqual(prestamos["Bicicleta Propia"].monto_mensual, Decimal("2000"))
        self.assertContains(response, "Ingreso mensual: $3\u00a0000")
        self.assertContains(response, "Costo mensual: $2\u00a0000")
        self.assertNotContains(response, "Bicicleta Ajena")

    def test_no_se_puede_borrar_un_prestamo_de_otro_usuario(self):
        otro = User.objects.create_user(username="otro_prestamo")
        prestamo = Prestamo.objects.create(
            user=otro,
            nombre="Privado",
            tipo="PERSONAL",
            monto_total=Decimal("100"),
        )

        response = self.client.post(reverse("borrar_prestamo", args=[prestamo.id]))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Prestamo.objects.filter(pk=prestamo.id).exists())
