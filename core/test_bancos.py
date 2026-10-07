"""Regresiones de permisos y conservación del catálogo global de bancos."""

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from banca.models import CredencialBanco
from configuracion.models import Banco, ConfiguracionGeneral, Producto


class CatalogoBancosTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("catalogo_user")
        self.other = User.objects.create_user("catalogo_other")
        self.staff = User.objects.create_user("catalogo_staff", is_staff=True)
        self.banco = Banco.objects.create(nombre="Banco compartido", notas="Nota pública")
        self.producto = Producto.objects.create(
            user=self.other, banco=self.banco, nombre="Cuenta ajena", tipo="CUENTA"
        )

    def test_usuario_no_puede_mutar_catalogo_ni_productos_ajenos(self):
        self.client.force_login(self.user)
        operaciones = (
            (reverse("crear_banco"), {"nombre": "Banco no autorizado"}),
            (reverse("editar_banco", args=[self.banco.pk]), {"nombre": "Alterado"}),
            (reverse("borrar_banco", args=[self.banco.pk]), {}),
        )
        for url, datos in operaciones:
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, datos).status_code, 403)
        self.banco.refresh_from_db()
        self.assertEqual(self.banco.nombre, "Banco compartido")
        self.assertTrue(Producto.objects.filter(pk=self.producto.pk).exists())
        self.assertEqual(Banco.objects.count(), 1)

    def test_staff_crea_edita_y_elimina_banco_sin_vinculos(self):
        self.client.force_login(self.staff)
        self.assertRedirects(self.client.post(reverse("crear_banco"), {
            "nombre": "Nuevo banco", "notas": "Información del catálogo"
        }), reverse("configuracion"), fetch_redirect_response=False)
        banco = Banco.objects.get(nombre="Nuevo banco")
        self.assertRedirects(self.client.post(reverse("editar_banco", args=[banco.pk]), {
            "nombre": "Banco renombrado", "notas": "Nueva nota"
        }), reverse("configuracion"), fetch_redirect_response=False)
        banco.refresh_from_db()
        self.assertEqual((banco.nombre, banco.notas), ("Banco renombrado", "Nueva nota"))
        self.assertRedirects(self.client.post(reverse("borrar_banco", args=[banco.pk])),
                             reverse("configuracion"), fetch_redirect_response=False)
        self.assertFalse(Banco.objects.filter(pk=banco.pk).exists())

    def test_staff_no_elimina_banco_con_productos_de_otro_usuario(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("borrar_banco", args=[self.banco.pk]), follow=True)
        self.assertContains(response, "No se puede eliminar una entidad financiera en uso")
        self.assertTrue(Banco.objects.filter(pk=self.banco.pk).exists())
        self.assertTrue(Producto.objects.filter(pk=self.producto.pk, user=self.other).exists())

    def test_preserva_credenciales_y_preferencias_sin_productos(self):
        self.client.force_login(self.staff)
        for tipo in ("credencial", "preferencia"):
            with self.subTest(tipo=tipo):
                banco = Banco.objects.create(nombre=f"Banco {tipo}")
                if tipo == "credencial":
                    vinculo = CredencialBanco.objects.create(user=self.other, banco=banco, conector="binance")
                else:
                    vinculo = ConfiguracionGeneral.objects.create(user=self.other, banco_defecto_salario=banco)
                response = self.client.post(reverse("borrar_banco", args=[banco.pk]))
                self.assertEqual(response.status_code, 302)
                vinculo.refresh_from_db()
                self.assertEqual(vinculo.banco_id if tipo == "credencial" else vinculo.banco_defecto_salario_id, banco.pk)
                self.assertTrue(Banco.objects.filter(pk=banco.pk).exists())

    def test_catalogo_muestra_acciones_solo_a_staff_y_productos_propios(self):
        propio = Producto.objects.create(user=self.user, banco=self.banco, nombre="Cuenta propia", tipo="CUENTA")
        self.client.force_login(self.user)
        response = self.client.get(reverse("configuracion"))
        self.assertContains(response, propio.nombre)
        self.assertNotContains(response, self.producto.nombre)
        self.assertNotContains(response, 'id="modal-banco"')
        self.assertNotContains(response, 'onclick="newBanco()"')
        self.assertNotContains(response, f'action="{reverse("borrar_banco", args=[self.banco.pk])}"')
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("configuracion")), 'id="modal-banco"')

    def test_anonimo_requiere_login_y_staff_no_modifica_con_get(self):
        self.assertEqual(self.client.post(reverse("crear_banco"), {"nombre": "Anónimo"}).status_code, 302)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("borrar_banco", args=[self.banco.pk])).status_code, 405)
        self.assertEqual(self.client.post(reverse("editar_banco", args=[999999]), {"nombre": "No existe"}).status_code, 404)

    def test_admin_no_ofrece_eliminar_entidad_en_uso(self):
        from django.contrib.admin.sites import AdminSite
        from django.test import RequestFactory
        from configuracion.admin import BancoAdmin

        admin_user = User.objects.create_superuser("catalogo_admin", "admin@example.test", "secret")
        request = RequestFactory().get("/")
        request.user = admin_user
        banco_admin = BancoAdmin(Banco, AdminSite())
        self.assertFalse(banco_admin.has_delete_permission(request, self.banco))
        vacio = Banco.objects.create(nombre="Sin relaciones")
        self.assertTrue(banco_admin.has_delete_permission(request, vacio))
        self.client.force_login(admin_user)
        response = self.client.post(reverse("admin:configuracion_banco_changelist"), {
            "action": "delete_selected", "_selected_action": [self.banco.pk, vacio.pk],
            "post": "yes",
        })
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Banco.objects.filter(pk=self.banco.pk).exists())
        self.assertTrue(Producto.objects.filter(pk=self.producto.pk).exists())
