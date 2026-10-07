from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from banca.models import VinculoPropiedadExterna
from departamentos.models import Departamento


class IdentificadoresDepartamentoTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="propiedades", password="x")
        self.depto = Departamento.objects.create(
            user=self.user,
            codigo="E107",
            piso="10",
            metros_cuadrados=Decimal("55.5"),
            valor_compra_uf=Decimal("3000"),
            valor_actual_uf=Decimal("3300"),
        )
        self.client.force_login(self.user)

    def _datos(self, codigo):
        return {
            "codigo": codigo,
            "piso": "10",
            "metros_cuadrados": "55.5",
            "valor_compra_uf": "3000",
            "valor_actual_uf": "3300",
            "comuna": "Concepción",
            "rol_sii": "681-236",
            "houm_id": "116938",
        }

    def test_editar_propiedad_guarda_rol_comuna_e_id_houm(self):
        respuesta = self.client.post(
            reverse("editar_departamento", args=[self.depto.pk]),
            self._datos("E107"),
        )

        self.assertRedirects(respuesta, reverse("departamentos"))
        self.depto.refresh_from_db()
        self.assertEqual(self.depto.rol_sii, "681-236")
        self.assertEqual(self.depto.comuna, "Concepción")
        vinculo = VinculoPropiedadExterna.objects.get(
            user=self.user, fuente="houm", external_property_id="116938"
        )
        self.assertEqual(vinculo.departamento_id, self.depto.pk)
        self.assertTrue(vinculo.activo)

    def test_crear_propiedad_acepta_identificadores_editables(self):
        respuesta = self.client.post(
            reverse("crear_departamento"),
            self._datos("F107") | {"rol_sii": "681-290", "houm_id": "116614"},
        )

        self.assertRedirects(respuesta, reverse("departamentos"))
        depto = Departamento.objects.get(user=self.user, codigo="F107")
        self.assertEqual(depto.rol_sii, "681-290")
        self.assertEqual(depto.comuna, "Concepción")
        self.assertTrue(
            VinculoPropiedadExterna.objects.filter(
                user=self.user,
                departamento=depto,
                external_property_id="116614",
                activo=True,
            ).exists()
        )

    def test_propiedades_ofrece_registro_manual_y_no_importador_municipal(self):
        pagina = self.client.get(reverse("departamentos"))

        self.assertContains(pagina, "Configurar obligaciones")
        self.assertContains(pagina, "Configurar manual")
        self.assertContains(pagina, "Sin configurar")
        self.assertNotContains(pagina, "Importar Aseo")
