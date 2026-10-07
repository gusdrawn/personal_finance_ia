from datetime import date
from decimal import Decimal
from html.parser import HTMLParser

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from banca.management.commands.seed_categorias_banca import sembrar
from banca.models import CategoriaTematica, MapeoComercio, MovimientoBancario
from banca.services.clasificacion_ia import _aplicar
from banca.services.transferencias import (
    CATEGORIA_TRANSFERENCIA_PROPIA, categoria_transferencia_propia,
)
from configuracion.models import Banco, Producto
from gastos.services import resumen_gastos
from prestamos.models import Prestamo
from patrimonio.models import CuentaPorCobrar


class OpcionesMovimientoParser(HTMLParser):
    """Comprueba qué formularios se muestran antes de desplegar más opciones."""

    def __init__(self, html):
        super().__init__()
        self.en_detalles = 0
        self.detalles = []
        self.formularios = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        atributos = dict(attrs)
        if tag == "details":
            self.en_detalles += 1
            self.detalles.append(atributos)
        elif tag == "form":
            self.formularios.append((atributos.get("action"), self.en_detalles > 0))

    def handle_endtag(self, tag):
        if tag == "details":
            self.en_detalles -= 1


class TransferenciasPropiasTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="traspasos")
        cls.other = User.objects.create_user(username="traspasos_ajenos")
        banco = Banco.objects.create(nombre="BCI traspasos")
        cls.cuenta = Producto.objects.create(
            user=cls.user, banco=banco, nombre="Cuenta BCI", tipo="CUENTA",
        )
        cls.servicios = CategoriaTematica.objects.create(
            user=cls.user, nombre="Servicios básicos", aplica_a="EGRESO",
        )

    def movimiento(self, monto, glosa="Traspaso BCI"):
        return MovimientoBancario.objects.create(
            user=self.user, producto=self.cuenta, fecha=date(2026, 8, 10),
            descripcion=glosa, monto=Decimal(monto), monto_clp=Decimal(monto),
            estado="FACTURADO", origen="PDF",
            hash_externo=f"traspaso-{MovimientoBancario.objects.count()}",
            por_revisar=True,
        )

    def test_categoria_disponible_en_ingresos_y_egresos_sin_sembrar(self):
        self.movimiento("-3000000")
        self.client.force_login(self.user)
        response = self.client.get(reverse("banca_movimientos"), {"pendientes": "1"})
        categoria = CategoriaTematica.objects.get(user=self.user, nombre=CATEGORIA_TRANSFERENCIA_PROPIA)
        self.assertEqual(categoria.aplica_a, "AMBOS")
        self.assertIn(categoria, response.context["categorias_ingreso"])
        self.assertIn(categoria, response.context["categorias_egreso"])
        self.assertContains(response, f'<option value="{categoria.pk}">{CATEGORIA_TRANSFERENCIA_PROPIA}</option>')
        self.assertContains(response, "Pago de tarjeta de crédito")
        self.client.get(reverse("banca_movimientos"))
        self.assertEqual(CategoriaTematica.objects.filter(user=self.user, nombre=CATEGORIA_TRANSFERENCIA_PROPIA).count(), 1)
        self.assertFalse(CategoriaTematica.objects.filter(user=self.other, nombre=CATEGORIA_TRANSFERENCIA_PROPIA).exists())

    def test_semilla_incluye_categoria_ambos_y_es_idempotente(self):
        sembrar(self.user)
        self.assertEqual(sembrar(self.user), 0)
        self.assertEqual(categoria_transferencia_propia(self.user).aplica_a, "AMBOS")

    def test_pendiente_muestra_categoria_y_oculta_vinculos_sin_duplicar_traspaso(self):
        movimiento = self.movimiento("-3000000")
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Cobro propio")
        ajena = CuentaPorCobrar.objects.create(user=self.other, nombre="Cobro privado ajeno")
        self.client.force_login(self.user)
        response = self.client.get(reverse("banca_movimientos"), {"pendientes": "1"})
        parser = OpcionesMovimientoParser(response.content.decode())
        resolver = reverse("banca_resolver_movimiento", args=[movimiento.pk])
        self.assertIn((resolver, False), parser.formularios)
        self.assertIn((resolver, True), parser.formularios)
        self.assertIn((reverse("banca_asignar_abono_cuenta_cobrar", args=[movimiento.pk]), True), parser.formularios)
        self.assertIn((reverse("banca_asignar_prestamo", args=[movimiento.pk]), True), parser.formularios)
        self.assertTrue(parser.detalles)
        self.assertTrue(all("open" not in atributos for atributos in parser.detalles))
        self.assertNotContains(response, '<option value="transferencia_propia"')
        self.assertContains(response, "Clasificar abono…")
        self.assertContains(response, cuenta.nombre)
        self.assertNotContains(response, ajena.nombre)

    def test_clasificado_mantiene_categoria_visible_y_opciones_cerradas(self):
        movimiento = self.movimiento("10000")
        movimiento.categoria = self.servicios
        movimiento.por_revisar = False
        movimiento.save(update_fields=["categoria", "por_revisar"])
        self.client.force_login(self.user)
        response = self.client.get(reverse("banca_movimientos"))
        parser = OpcionesMovimientoParser(response.content.decode())
        self.assertIn((reverse("banca_reclasificar", args=[movimiento.pk]), False), parser.formularios)
        self.assertIn((reverse("banca_asignar_prestamo", args=[movimiento.pk]), True), parser.formularios)
        self.assertTrue(parser.detalles)
        self.assertTrue(all("open" not in atributos for atributos in parser.detalles))
        self.assertNotContains(response, '<option value="transferencia_propia"')
        self.assertContains(response, "Más opciones")

    def test_trafico_bci_cuenta_solo_pagos_reales_en_consumo_y_caja(self):
        categoria = categoria_transferencia_propia(self.user)
        self.client.force_login(self.user)
        for monto in ("3000000", "-3000000", "2200000", "-2200000"):
            movimiento = self.movimiento(monto)
            response = self.client.post(reverse("banca_resolver_movimiento", args=[movimiento.pk]), {
                "decision": "gasto" if Decimal(monto) > 0 else "ingreso",
                "categoria": categoria.pk,
            })
            self.assertEqual(response.status_code, 302)
            movimiento.refresh_from_db()
            self.assertEqual(movimiento.naturaleza, "TRANSFERENCIA")
            self.assertFalse(movimiento.por_revisar)
            self.assertTrue(movimiento.naturaleza_es_manual)
            self.assertTrue(movimiento.categoria_es_manual)
        pago = self.movimiento("800000", "Pago de servicios")
        self.client.post(reverse("banca_resolver_movimiento", args=[pago.pk]), {
            "decision": "gasto", "categoria": self.servicios.pk,
        })
        self.assertFalse(MapeoComercio.objects.filter(user=self.user, categoria=categoria).exists())
        for modo in ("consumo", "caja"):
            with self.subTest(modo=modo):
                resumen = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), modo)
                self.assertEqual(resumen["totales"]["ingresos_clp"], Decimal("0"))
                self.assertEqual(resumen["totales"]["egresos_clp"], Decimal("800000"))

    def test_reclasificar_transferencia_desvincula_bicicleta_y_puede_revertirse(self):
        categoria = categoria_transferencia_propia(self.user)
        movimiento = self.movimiento("10000")
        movimiento.prestamo = Prestamo.objects.create(
            user=self.user, nombre="Bicicleta anterior", tipo="TERCEROS", monto_total=10000,
        )
        movimiento.save(update_fields=["prestamo"])
        self.client.force_login(self.user)
        ruta = reverse("banca_reclasificar", args=[movimiento.pk])
        self.client.post(ruta, {"categoria": categoria.pk})
        movimiento.refresh_from_db()
        self.assertIsNone(movimiento.prestamo_id)
        self.assertFalse(movimiento.contabiliza)
        self.assertFalse(MapeoComercio.objects.filter(user=self.user, categoria=categoria).exists())
        self.client.post(ruta, {"categoria": self.servicios.pk})
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.naturaleza, "OPERACIONAL")
        self.assertTrue(movimiento.contabiliza)

    def test_selector_separa_transferencia_propia_de_pago_tarjeta_y_permita_revertir(self):
        movimiento = self.movimiento("-3000000")
        self.client.force_login(self.user)
        ruta = reverse("banca_asignar_prestamo", args=[movimiento.pk])
        self.client.post(ruta, {"prestamo": "transferencia_propia"})
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.categoria.nombre, CATEGORIA_TRANSFERENCIA_PROPIA)
        self.assertEqual(movimiento.naturaleza, "TRANSFERENCIA")
        self.assertFalse(movimiento.por_revisar)
        self.assertFalse(MapeoComercio.objects.filter(user=self.user).exists())
        self.client.post(ruta, {"prestamo": ""})
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.naturaleza, "OPERACIONAL")
        self.assertIsNone(movimiento.categoria_id)

    def test_sugerencia_ia_requiere_revision_para_no_confundir_abono_real(self):
        categoria = categoria_transferencia_propia(self.user)
        movimiento = self.movimiento("-3000000")
        movimiento.por_revisar = False
        movimiento.save(update_fields=["por_revisar"])
        _aplicar(movimiento, categoria, "IA", 0.95)
        movimiento.refresh_from_db()
        self.assertTrue(movimiento.por_revisar)
        self.assertFalse(movimiento.contabiliza)
        self.assertEqual(movimiento.naturaleza, "OPERACIONAL")

    def test_no_permite_categoria_ni_movimiento_de_otro_usuario(self):
        ajena = categoria_transferencia_propia(self.other)
        movimiento = self.movimiento("-3000000")
        self.client.force_login(self.user)
        for ruta, datos in (
            ("banca_reclasificar", {"categoria": ajena.pk}),
            ("banca_resolver_movimiento", {"categoria": ajena.pk, "decision": "ingreso"}),
        ):
            self.assertEqual(self.client.post(reverse(ruta, args=[movimiento.pk]), datos).status_code, 404)
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse("banca_asignar_prestamo", args=[movimiento.pk]), {
            "prestamo": "transferencia_propia",
        }).status_code, 404)
        movimiento.refresh_from_db()
        self.assertIsNone(movimiento.categoria_id)
        self.assertEqual(movimiento.naturaleza, "OPERACIONAL")
