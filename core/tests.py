"""Tests del core: cálculo de vencimientos próximos para recordatorios del Calendario."""

from datetime import date
from decimal import Decimal
import os
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory, TestCase, override_settings
from django.template.loader import render_to_string
from django.urls import set_script_prefix
from django.urls import reverse

from core.services.recordatorios import (
    proxima_ocurrencia_gasto,
    proximo_dia_cobro,
    vencimientos_proximos,
)
from gastos.models import GastoProgramado
from banca.models import ApplePayApiKey
from configuracion.models import CredencialIntegracion
from configuracion.services.integrations import get_coinmarketcap_api_key


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
        items = vencimientos_proximos(self.user, dias=3, hoy=hoy)
        nombres = [i["nombre"] for i in items]
        self.assertEqual(nombres, ["Netflix"])
        self.assertNotIn("Seguro", nombres)


class QuickLinksTemplateTest(TestCase):
    """El menú compartido conserva todos los accesos externos y su apertura segura."""

    QUICK_LINKS = {
        "Santander": "https://banco.santander.cl/",
        "Scotiabank": "https://www.scotiabankchile.cl/",
        "Bci": "https://www.bci.cl/",
        "Banco Falabella": "https://www.bancofalabella.cl/",
        "Itaú": "https://www.itau.cl/",
        "AFP Uno": "https://www.uno.cl/",
        "AFC": "https://www.afc.cl/",
        "CMF deudas": "https://conocetudeuda.cmfchile.cl/",
        "Fintual": "https://fintual.cl/",
        "DVA": "https://www.dvacapital.com/home",
        "SoyFocus": "https://www.soyfocus.com/",
        "BTG Pactual": "https://www.btgpactual.cl/",
    }

    def test_renderiza_accesos_rapidos_para_usuario_autenticado(self):
        user = User.objects.create_user(username="quick_links", password="secret")
        request = RequestFactory().get("/dashboard/")
        request.user = user
        request.resolver_match = SimpleNamespace(url_name="dashboard")

        rendered = render_to_string(
            "base.html",
            {"request": request, "user": user, "messages": []},
        )

        self.assertIn("Accesos rápidos", rendered)
        self.assertIn('class="app-header-title"', rendered)
        self.assertIn('class="app-header-user"', rendered)
        self.assertIn('class="quick-links-navbar"', rendered)
        self.assertLess(
            rendered.index('class="app-header-title"'),
            rendered.index('class="quick-links-navbar"'),
        )
        self.assertLess(
            rendered.index('class="quick-links-navbar"'),
            rendered.index('class="app-header-user"'),
        )
        self.assertNotIn('class="sidebar-quick-links"', rendered)
        self.assertEqual(rendered.count('class="quick-links-group"'), 3)
        self.assertIn('aria-label="Bancos"', rendered)
        self.assertIn('aria-label="Previsión y deudas"', rendered)
        self.assertIn('aria-label="Inversiones"', rendered)
        for label, url in self.QUICK_LINKS.items():
            self.assertIn(f'href="{url}"', rendered)
            self.assertIn(f'class="quick-link-label">{label}</span>', rendered)
        self.assertEqual(rendered.count('quick-link-icon"'), len(self.QUICK_LINKS))
        self.assertEqual(rendered.count('target="_blank"'), len(self.QUICK_LINKS))
        self.assertEqual(rendered.count('rel="noopener noreferrer"'), len(self.QUICK_LINKS))

    def test_no_expone_accesos_rapidos_a_usuarios_anonimos(self):
        request = RequestFactory().get("/accounts/login/")
        request.user = AnonymousUser()
        request.resolver_match = SimpleNamespace(url_name="login")

        rendered = render_to_string(
            "base.html",
            {"request": request, "user": request.user, "messages": []},
        )

        self.assertNotIn("Accesos rápidos", rendered)


class ApplePayConfigurationTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="apple_config", password="secret")
        self.other_user = User.objects.create_user(username="apple_config_other", password="secret")
        self.client.force_login(self.user)

    def test_configuracion_muestra_key_por_usuario_sin_exponer_secreto(self):
        key, secret = ApplePayApiKey.crear(self.user, nombre="iPhone personal")

        response = self.client.get(reverse("configuracion"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Apple Pay y Atajos")
        self.assertContains(response, key.prefijo)
        self.assertNotContains(response, secret)
        self.assertNotContains(response, "apple_config_other")

    def test_crear_key_la_muestra_una_vez_y_la_guarda_hasheada(self):
        response = self.client.post(
            reverse("crear_apple_pay_key"),
            {"nombre": "Atajo de compras"},
        )
        self.assertRedirects(
            response,
            reverse("configuracion"),
            fetch_redirect_response=False,
        )
        secret = response.wsgi_request.session["apple_pay_new_key"]["secret"]
        key = ApplePayApiKey.objects.get(user=self.user, nombre="Atajo de compras")

        first_view = self.client.get(reverse("configuracion"))
        second_view = self.client.get(reverse("configuracion"))

        self.assertContains(first_view, secret)
        self.assertNotContains(second_view, secret)
        self.assertNotEqual(key.clave_hash, secret)

    def test_revocar_key_solo_afecta_al_usuario_actual(self):
        own_key, _ = ApplePayApiKey.crear(self.user, nombre="Propia")
        other_key, _ = ApplePayApiKey.crear(self.other_user, nombre="Ajena")

        response = self.client.post(
            reverse("revocar_apple_pay_key", args=[own_key.id])
        )

        own_key.refresh_from_db()
        other_key.refresh_from_db()
        self.assertRedirects(response, reverse("configuracion"))
        self.assertFalse(own_key.activo)
        self.assertIsNotNone(own_key.revocado_at)
        self.assertTrue(other_key.activo)

        forbidden = self.client.post(
            reverse("revocar_apple_pay_key", args=[other_key.id])
        )
        self.assertEqual(forbidden.status_code, 404)

    def test_nombre_largo_se_recorta_al_limite_del_modelo(self):
        self.client.post(
            reverse("crear_apple_pay_key"),
            {"nombre": "x" * 200},
        )

        key = ApplePayApiKey.objects.get(user=self.user)
        self.assertEqual(len(key.nombre), 80)


class MarketIntegrationConfigurationTest(TestCase):
    FERNET_KEY = Fernet.generate_key().decode()

    def setUp(self):
        self.staff = User.objects.create_user(username="market_staff", password="secret", is_staff=True)
        self.viewer = User.objects.create_user(username="market_viewer", password="secret")
        self.environment = patch.dict(os.environ, {"BANCA_FERNET_KEY": self.FERNET_KEY}, clear=False)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    @override_settings(BANCA_FERNET_KEY=FERNET_KEY)
    def test_staff_configura_todas_las_integraciones_desde_ajustes(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("configuracion"))

        self.assertContains(response, "Integraciones de mercado")
        self.assertContains(response, "CoinGecko")
        self.assertContains(response, "CoinMarketCap")
        self.assertContains(response, "Tokenomist")
        self.assertContains(response, "Brave Search")

    @override_settings(BANCA_FERNET_KEY=FERNET_KEY)
    def test_guarda_coinmarketcap_cifrada_y_el_resolver_la_usa(self):
        self.client.force_login(self.staff)
        secret = "cmc-secret-key-123456"
        response = self.client.post(
            reverse("guardar_market_api_key", args=[CredencialIntegracion.PROVIDER_COINMARKETCAP]),
            {"api_key": secret},
        )

        self.assertRedirects(response, reverse("configuracion"), fetch_redirect_response=False)
        credential = CredencialIntegracion.objects.get(provider=CredencialIntegracion.PROVIDER_COINMARKETCAP)
        self.assertNotIn(secret.encode(), bytes(credential.api_key_cifrada))
        self.assertEqual(credential.key_prefix, secret[:6])
        self.assertEqual(get_coinmarketcap_api_key(), secret)
        self.assertEqual(credential.updated_by, self.staff)

    @override_settings(BANCA_FERNET_KEY=FERNET_KEY)
    def test_usuario_normal_no_ve_ni_modifica_integraciones_globales(self):
        self.client.force_login(self.viewer)
        page = self.client.get(reverse("configuracion"))
        self.assertNotContains(page, "Integraciones de mercado")

        response = self.client.post(
            reverse("guardar_market_api_key", args=[CredencialIntegracion.PROVIDER_COINMARKETCAP]),
            {"api_key": "una-clave-no-autorizada"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(CredencialIntegracion.objects.exists())

    @override_settings(BANCA_FERNET_KEY=FERNET_KEY)
    def test_retirar_clave_impide_que_el_scanner_la_resuelva(self):
        credential = CredencialIntegracion.objects.create(
            provider=CredencialIntegracion.PROVIDER_COINMARKETCAP,
            updated_by=self.staff,
        )
        credential.set_api_key("cmc-key-to-remove")
        credential.save()
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("retirar_market_api_key", args=[CredencialIntegracion.PROVIDER_COINMARKETCAP]),
        )

        credential.refresh_from_db()
        self.assertRedirects(response, reverse("configuracion"), fetch_redirect_response=False)
        self.assertFalse(credential.active)
        self.assertIsNone(credential.api_key_cifrada)
        self.assertEqual(get_coinmarketcap_api_key(), "")

    def test_variable_coinmarketcap_del_entorno_no_se_usa(self):
        with patch.dict(os.environ, {"COINMARKETCAP_API_KEY": "legacy-env-key"}, clear=False):
            self.assertEqual(get_coinmarketcap_api_key(), "")


class PassengerMountRoutingTest(TestCase):
    """La raíz de la app conserva el prefijo cuando Passenger la monta."""

    @override_settings(
        FORCE_SCRIPT_NAME="/finance",
        ALLOWED_HOSTS=["finance.test"],
    )
    def test_root_redirects_inside_finance_mount(self):
        set_script_prefix("/finance/")
        try:
            response = self.client.get(
                "/",
                SCRIPT_NAME="/finance",
                HTTP_HOST="finance.test",
            )

            self.assertRedirects(
                response,
                "/finance/dashboard/",
                fetch_redirect_response=False,
            )
        finally:
            set_script_prefix("/")
