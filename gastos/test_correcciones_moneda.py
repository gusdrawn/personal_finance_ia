"""Regresiones de fusiones, fechas y edición parcial de importes."""

import json
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from banca.models import CategoriaTematica, MovimientoBancario
from configuracion.models import Banco, Producto, TipoCambio
from gastos.models import MovimientoManual
from gastos.services import convertir_a_clp


class CorreccionesMonedaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("moneda_gastos")
        self.other = User.objects.create_user("moneda_gastos_other")
        self.origen = CategoriaTematica.objects.create(user=self.user, nombre="Origen", aplica_a="EGRESO")
        self.destino = CategoriaTematica.objects.create(user=self.user, nombre="Destino", aplica_a="EGRESO")
        banco = Banco.objects.create(nombre="Banco moneda")
        self.producto = Producto.objects.create(user=self.user, banco=banco, nombre="Cuenta", tipo="CUENTA")
        self.client.force_login(self.user)

    def manual(self, categoria, *, monto="100", moneda="USD", clp="90000", resumen=True, **extra):
        return MovimientoManual.objects.create(user=self.user, categoria=categoria,
            fecha=date(2026, 9, 10), periodo=date(2026, 9, 1), direccion="EGRESO",
            monto=Decimal(monto), moneda=moneda, monto_clp=Decimal(clp),
            descripcion="Gasto", es_resumen_mensual=resumen, **extra)

    def fusionar(self, destino=None):
        return self.client.post(reverse("categoria-gasto-fusionar", args=[self.origen.pk]),
            json.dumps({"destino": (destino or self.destino).pk}), content_type="application/json")

    def test_fusion_misma_moneda_conserva_importes_y_conversion_agregada(self):
        self.manual(self.origen, monto="70", clp="63000", tipo_cambio=900, monto_clp_estimado=True)
        destino = self.manual(self.destino, monto="30", clp="30000", tipo_cambio=1000)
        self.assertEqual(self.fusionar().status_code, 200)
        destino.refresh_from_db()
        self.assertEqual(destino.monto, Decimal("100"))
        self.assertEqual(destino.monto_clp, Decimal("93000"))
        self.assertEqual(destino.tipo_cambio, Decimal("930"))
        self.assertTrue(destino.monto_clp_estimado)
        self.assertEqual(MovimientoManual.objects.filter(user=self.user).count(), 1)

    def test_fusion_distinta_moneda_se_rechaza_sin_efectos_parciales(self):
        origen = self.manual(self.origen, monto="100000", moneda="CLP", clp="100000")
        destino = self.manual(self.destino)
        banco = MovimientoBancario.objects.create(user=self.user, producto=self.producto,
            fecha=origen.fecha, descripcion="No mover", monto=10, monto_clp=10,
            categoria=self.origen, hash_externo="fusion-sin-efectos")
        self.assertEqual(self.fusionar().status_code, 400)
        origen.refresh_from_db(); destino.refresh_from_db(); banco.refresh_from_db(); self.origen.refresh_from_db()
        self.assertEqual(origen.categoria_id, self.origen.pk)
        self.assertEqual(destino.monto, Decimal("100"))
        self.assertEqual(banco.categoria_id, self.origen.pk)
        self.assertTrue(self.origen.activa)

    def test_no_fusiona_direcciones_incompatibles_ni_categorias_ajenas(self):
        ingreso = CategoriaTematica.objects.create(user=self.user, nombre="Ingresos", aplica_a="INGRESO")
        ajena = CategoriaTematica.objects.create(user=self.other, nombre="Ajena", aplica_a="EGRESO")
        self.assertEqual(self.fusionar(ingreso).status_code, 400)
        self.assertEqual(self.fusionar(ajena).status_code, 404)
        self.origen.refresh_from_db()
        self.assertTrue(self.origen.activa)

    def test_conversion_no_usa_tasas_futuras_o_fuentes_no_oficiales(self):
        TipoCambio.objects.create(fecha=date(2026, 9, 11), dolar=900, uf=40000)
        TipoCambio.objects.create(fecha=date(2026, 9, 9), fuente="otra", dolar=500, uf=20000)
        self.assertEqual(convertir_a_clp(Decimal("10"), "USD", date(2026, 9, 10)), (Decimal("0"), None, True))
        TipoCambio.objects.create(fecha=date(2026, 9, 8), dolar=800, uf=39000)
        self.assertEqual(convertir_a_clp(Decimal("10"), "USD", date(2026, 9, 10)), (Decimal("8000"), Decimal("800"), True))

    def test_patch_descripcion_preserva_periodo_y_conversion_original(self):
        manual = self.manual(self.origen, resumen=False, tipo_cambio=900)
        manual.periodo = date(2026, 8, 1); manual.save()
        TipoCambio.objects.create(fecha=date(2026, 9, 10), dolar=1000, uf=40000)
        response = self.client.patch(reverse("movimiento-manual-detail", args=[manual.pk]),
            json.dumps({"descripcion": "Descripción corregida"}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        manual.refresh_from_db()
        self.assertEqual(manual.periodo, date(2026, 8, 1))
        self.assertEqual(manual.monto_clp, Decimal("90000"))
        self.assertEqual(manual.tipo_cambio, Decimal("900"))

    def test_patch_importe_recalcula_y_periodo_explicito_se_normaliza(self):
        manual = self.manual(self.origen, resumen=False, tipo_cambio=900)
        TipoCambio.objects.create(fecha=date(2026, 9, 10), dolar=1000, uf=40000)
        response = self.client.patch(reverse("movimiento-manual-detail", args=[manual.pk]),
            json.dumps({"monto": "200", "periodo": "2026-08-20"}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        manual.refresh_from_db()
        self.assertEqual(manual.periodo, date(2026, 8, 1))
        self.assertEqual(manual.monto_clp, Decimal("200000"))

    def test_patch_sin_tasa_disponible_conserva_conversion_ya_registrada(self):
        manual = self.manual(self.origen, resumen=False, tipo_cambio=900)
        response = self.client.patch(reverse("movimiento-manual-detail", args=[manual.pk]),
            json.dumps({"notas": "Referencia nueva"}), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        manual.refresh_from_db()
        self.assertEqual(manual.monto_clp, Decimal("90000"))
        self.assertEqual(manual.tipo_cambio, Decimal("900"))
