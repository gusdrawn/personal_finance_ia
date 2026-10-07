"""El admin edita saldos originales y conserva las proyecciones de compatibilidad."""

from datetime import date
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from configuracion.models import TipoCambio
from patrimonio.models import Activo, CuentaPorCobrar, Pasivo


class AdminSaldosTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("admin_saldos", "admin@example.test", "secret")
        self.request = RequestFactory().post("/")
        self.request.user = self.user
        TipoCambio.objects.create(fecha=date(2026, 9, 1), dolar=900, uf=40000)
        TipoCambio.objects.create(fecha=date(2099, 1, 1), dolar=2000, uf=80000)

    def formulario(self, obj, **extra):
        datos = {"user": self.user.pk, "nombre": "Saldo corregido", "tipo": obj.tipo,
            "horizonte_temporal": "EFECTIVO", "es_liquido": "on", "activo": "on",
            "monto_original": "200", "moneda_original": "USD",
            "monto_clp": "5", "monto_usd": "5"}
        datos.update(extra)
        model_admin = admin.site._registry[type(obj)]
        return model_admin, model_admin.get_form(self.request, obj)(data=datos, instance=obj)

    def test_activo_y_pasivo_editan_original_y_derivan_equivalentes(self):
        for modelo, tipo in ((Activo, "EFECTIVO"), (Pasivo, "OTRO")):
            with self.subTest(modelo=modelo.__name__):
                obj = modelo.objects.create(user=self.user, nombre="Saldo", tipo=tipo,
                    monto_original=100, moneda_original="USD", monto_clp=90000, monto_usd=100)
                model_admin, form = self.formulario(obj)
                self.assertTrue(form.is_valid(), form.errors.as_json())
                model_admin.save_model(self.request, form.save(commit=False), form, True)
                obj.refresh_from_db()
                self.assertEqual(obj.monto_original, Decimal("200"))
                self.assertEqual(obj.monto_clp, Decimal("180000"))
                self.assertEqual(obj.monto_usd, Decimal("200"))
                self.assertNotIn("monto_clp", form.fields)

    def test_nuevo_saldo_requiere_moneda_y_monto_y_rechaza_negativos(self):
        for importe, moneda in (("", "USD"), ("100", ""), ("", ""), ("-1", "CLP")):
            with self.subTest(importe=importe, moneda=moneda):
                _, form = self.formulario(Activo(user=self.user, tipo="EFECTIVO"),
                    monto_original=importe, moneda_original=moneda)
                self.assertFalse(form.is_valid())

    def test_legacy_sin_moneda_conserva_valores_hasta_revision_explicita(self):
        obj = Activo.objects.create(user=self.user, nombre="Legacy", tipo="EFECTIVO",
            monto_clp=90000, monto_usd=100, requiere_revision_moneda=True)
        model_admin, form = self.formulario(obj, monto_original="", moneda_original="")
        self.assertTrue(form.is_valid(), form.errors.as_json())
        model_admin.save_model(self.request, form.save(commit=False), form, True)
        obj.refresh_from_db()
        self.assertIsNone(obj.monto_original)
        self.assertIsNone(obj.moneda_original)
        self.assertEqual(obj.monto_clp, Decimal("90000"))
        self.assertTrue(obj.requiere_revision_moneda)

    def test_pasivo_sincronizado_no_se_sobrescribe_desde_el_admin(self):
        obj = Pasivo.objects.create(user=self.user, nombre="Sincronizado", tipo="OTRO",
            sincronizado_banca=True, monto_original=100, moneda_original="USD",
            monto_clp=90000, monto_usd=100)
        model_admin, form = self.formulario(obj)
        self.assertNotIn("monto_original", form.fields)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        model_admin.save_model(self.request, form.save(commit=False), form, True)
        obj.refresh_from_db()
        self.assertEqual(obj.monto_original, Decimal("100"))
        self.assertEqual(obj.monto_clp, Decimal("90000"))
        self.assertTrue(obj.sincronizado_banca)

    def test_activos_de_fuentes_y_cobros_conservan_saldo_automatico(self):
        importado = Activo.objects.create(user=self.user, nombre="Binance", tipo="CRIPTO",
            fuente_externa="binance", monto_original=100, moneda_original="USD",
            monto_clp=90000, monto_usd=100)
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Cobro automático")
        for obj in (importado, cuenta.activo_patrimonial):
            with self.subTest(tipo=obj.tipo):
                original = obj.monto_original
                model_admin, form = self.formulario(obj)
                self.assertNotIn("monto_original", form.fields)
                self.assertTrue(form.is_valid(), form.errors.as_json())
                model_admin.save_model(self.request, form.save(commit=False), form, True)
                obj.refresh_from_db()
                self.assertEqual(obj.monto_original, original)
