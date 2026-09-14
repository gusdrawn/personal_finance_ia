import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from banca.models import ApplePayApiKey, ApplePayEvent, CategoriaTematica, MapeoComercio, MovimientoBancario
from banca.services.apple_pay import registrar_pago
from configuracion.models import Banco, Producto


class ApplePayPocTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="apple_pay_user", password="x")
        self.other_user = User.objects.create_user(username="apple_pay_other", password="x")
        self.key, self.secret = ApplePayApiKey.crear(self.user)
        self.other_key, self.other_secret = ApplePayApiKey.crear(self.other_user)
        self.banco = Banco.objects.create(nombre="Banco Apple Pay Test")
        self.producto = Producto.objects.create(
            user=self.user, banco=self.banco, nombre="TDC Test *1234", tipo="TDC", ultimos4="1234"
        )

    def payload(self, **changes):
        data = {
            "event_id": "shortcut-event-001",
            "occurred_at": "2026-08-24T14:32:00-04:00",
            "merchant": "Supermercado Central 123456",
            "amount": "12990",
            "currency": "CLP",
            "card_last4": "1234",
            "product_id": self.producto.id,
            "notify": False,
        }
        data.update(changes)
        return data

    def post_ingest(self, secret, payload=None):
        return self.client.post(
            reverse("banca_apple_pay_ingest"),
            data=json.dumps(payload or self.payload()),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {secret}",
        )

    def test_crea_movimiento_clasificado_por_regla_y_respeta_usuario(self):
        categoria = CategoriaTematica.objects.create(
            user=self.user, nombre="Supermercado", esencialidad_defecto="FIJO"
        )
        MapeoComercio.objects.create(
            user=self.user,
            patron="supermercado central",
            categoria=categoria,
            origen="MANUAL",
        )

        response = self.post_ingest(self.secret)

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["classification"]["source"], "REGLA")
        self.assertEqual(body["classification"]["category"], "Supermercado")
        movimiento = MovimientoBancario.objects.get(user=self.user)
        self.assertEqual(movimiento.fecha, date(2026, 8, 24))
        self.assertEqual(movimiento.monto_clp, Decimal("12990"))
        self.assertEqual(movimiento.producto_id, self.producto.id)
        self.assertEqual(MovimientoBancario.objects.filter(user=self.other_user).count(), 0)

    @patch("banca.services.notificador.avisar_nuevos_no_facturados", return_value=True)
    def test_reintento_del_mismo_evento_es_idempotente_y_no_realerta(self, avisar):
        payload = self.payload(notify=True)
        first = self.post_ingest(self.secret, payload)
        second = self.post_ingest(self.secret, payload)

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["duplicated"])
        self.assertEqual(MovimientoBancario.objects.filter(user=self.user).count(), 1)
        self.assertEqual(ApplePayEvent.objects.filter(user=self.user).count(), 1)
        avisar.assert_called_once()

    def test_mismo_event_id_con_datos_diferentes_es_conflicto(self):
        self.assertEqual(self.post_ingest(self.secret).status_code, 201)

        response = self.post_ingest(self.secret, self.payload(amount="13990"))

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "event_id_conflict")
        self.assertEqual(MovimientoBancario.objects.filter(user=self.user).count(), 1)

    def test_payload_invalido_no_persiste_y_devuelve_error_de_campo(self):
        response = self.post_ingest(self.secret, self.payload(amount="0"))

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["field"], "amount")
        self.assertEqual(MovimientoBancario.objects.count(), 0)
        self.assertEqual(ApplePayEvent.objects.count(), 0)

    def test_usd_puede_convertirse_con_tipo_de_cambio(self):
        response = self.post_ingest(
            self.secret,
            self.payload(
                event_id="shortcut-usd-001",
                amount="10.50",
                currency="USD",
                amount_clp=None,
                exchange_rate="950.25",
            ),
        )

        self.assertEqual(response.status_code, 201)
        movimiento = MovimientoBancario.objects.get(user=self.user)
        self.assertEqual(movimiento.moneda, "USD")
        self.assertEqual(movimiento.monto_clp, Decimal("9977.63"))

    def test_api_key_invalida_y_revocada_no_ingresa(self):
        invalid = self.post_ingest("fpa_apple_invalid")
        self.assertEqual(invalid.status_code, 401)

        self.client.force_login(self.user)
        revoke = self.client.post(
            reverse("banca_apple_pay_key_revoke", args=[self.key.id])
        )
        self.assertEqual(revoke.status_code, 200)
        self.assertEqual(self.post_ingest(self.secret).status_code, 401)

    def test_key_de_otro_usuario_a_isla_el_movimiento(self):
        producto_otro = Producto.objects.create(
            user=self.other_user, banco=self.banco, nombre="TDC otra *1234", tipo="TDC", ultimos4="1234"
        )
        response = self.post_ingest(
            self.other_secret,
            self.payload(event_id="other-user-event", merchant="Compra de otra persona", product_id=producto_otro.id),
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(MovimientoBancario.objects.filter(user=self.user).count(), 0)
        self.assertEqual(MovimientoBancario.objects.filter(user=self.other_user).count(), 1)
        self.assertEqual(ApplePayEvent.objects.filter(user=self.other_user).count(), 1)

    def test_clasificacion_ia_aprende_la_glosa_para_la_siguiente_transaccion(self):
        class FakeClassifier:
            def clasificar(self, glosas, categorias):
                return [{"glosa": "cafeteria centro", "categoria": "Cafetería", "confianza": 0.93}]

        result = registrar_pago(
            self.user,
            self.payload(
                event_id="shortcut-ai-001",
                merchant="CAFETERIA CENTRO 9876",
            ),
            api_key=self.key,
            clasificador=FakeClassifier(),
        )

        self.assertEqual(result["classification"]["source"], "IA")
        self.assertEqual(result["classification"]["category"], "Cafetería")
        self.assertTrue(
            MapeoComercio.objects.filter(
                user=self.user, patron="cafeteria centro", categoria__nombre="Cafetería"
            ).exists()
        )

    def test_creacion_de_key_por_endpoint_no_expone_el_hash(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("banca_apple_pay_keys"),
            data=json.dumps({"name": "iPhone personal"}),
            content_type="application/json; charset=utf-8",
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIn("api_key", body)
        self.assertNotIn("clave_hash", body)
        created = ApplePayApiKey.objects.get(pk=body["id"])
        self.assertNotEqual(created.clave_hash, body["api_key"])
