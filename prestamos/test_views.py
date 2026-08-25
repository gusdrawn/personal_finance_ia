"""Pruebas de regresión para la integración de bicicletas de terceros."""

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from gastos.models import CategoriaIngreso, RegistroMensual
from patrimonio.models import Activo
from prestamos.models import Prestamo


class PrestamoTercerosViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="prestamos_test", password="x")
        self.client.force_login(self.user)

    def test_crear_tercero_crea_categoria_registro_negativo_y_activo(self):
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
        prestamo = Prestamo.objects.get(user=self.user)
        categoria = CategoriaIngreso.objects.get(
            user=self.user, nombre="Bicicleta: Bicicleta Ana"
        )
        registro = RegistroMensual.objects.get(user=self.user)
        activo = Activo.objects.get(
            user=self.user, nombre="Bicicleta - Bicicleta Ana"
        )
        self.assertEqual(categoria.tipo, "TDC")
        self.assertEqual(registro.monto, Decimal("-150000"))
        self.assertEqual(registro.categoria_id, categoria.id)
        self.assertEqual(registro.notas, f"Abono Automático [BIK-{prestamo.id}]")
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
        registro = RegistroMensual.objects.get(user=self.user)
        self.assertEqual(activo.monto_clp, Decimal("175000"))
        self.assertEqual(registro.monto, Decimal("-175000"))
        self.assertEqual(registro.mes, 7)

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
