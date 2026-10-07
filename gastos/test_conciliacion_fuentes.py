from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from banca.models import CategoriaTematica, ImportacionEstadoCuenta, MovimientoBancario
from configuracion.models import Banco, Producto
from gastos.conciliaciones import conciliar_movimientos_manuales
from gastos.models import ConciliacionMovimientoManual, MovimientoManual
from gastos.services import resumen_gastos


class ConciliacionFuentesTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("fuentes")
        self.bank = Banco.objects.create(nombre="Banco fuentes")
        self.product = Producto.objects.create(user=self.user, banco=self.bank, nombre="Cuenta", tipo="CUENTA")
        self.cat = CategoriaTematica.objects.create(user=self.user, nombre="Compras", aplica_a="EGRESO")
        self.fecha = date(2026, 8, 10)

    def banco(self, **overrides):
        values = dict(user=self.user, producto=self.product, fecha=self.fecha, descripcion="Supermercado", monto=Decimal("10000"), monto_clp=Decimal("10000"), categoria=self.cat, estado="FACTURADO", hash_externo=f"banco-{MovimientoBancario.objects.count()}")
        values.update(overrides)
        return MovimientoBancario.objects.create(**values)

    def manual(self, **overrides):
        values = dict(user=self.user, fecha=self.fecha, periodo=date(2026, 8, 1), direccion="EGRESO", descripcion="Supermercado", monto=Decimal("10000"), monto_clp=Decimal("10000"), categoria=self.cat)
        values.update(overrides)
        return MovimientoManual.objects.create(**values)

    def total(self, perspectiva="caja"):
        return resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), perspectiva)["totales"]["egresos_clp"]

    def test_misma_fecha_e_importe_con_comercio_distinto_solo_sugiere(self):
        self.banco(descripcion="Ferretería")
        self.manual(descripcion="Supermercado efectivo")
        conciliar_movimientos_manuales(self.user)
        self.assertEqual(ConciliacionMovimientoManual.objects.get().estado, "SUGERIDA")
        self.assertEqual(self.total(), Decimal("20000"))

    def test_coincidencia_confirmada_por_usuario_se_cuenta_una_vez(self):
        self.banco(descripcion="SUPERMERCADO")
        self.manual(descripcion=" supermercado ")
        conciliar_movimientos_manuales(self.user)
        link = ConciliacionMovimientoManual.objects.get()
        self.assertEqual(link.estado, "SUGERIDA")
        self.assertEqual(self.total(), Decimal("20000"))
        self.client.force_login(self.user)
        result = self.client.post(reverse("movimiento-manual-resolver-conciliacion", args=[link.movimiento_manual_id]), {"accion": "confirmar", "conciliacion_id": link.pk})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(ConciliacionMovimientoManual.objects.get().estado, "CONFIRMADA")
        self.assertEqual(self.total(), Decimal("10000"))

    def test_tarjeta_no_facturada_no_oculta_efectivo_manual(self):
        self.product.tipo = "TDC"
        self.product.save()
        self.banco(estado="NO_FACTURADO")
        self.manual()
        conciliar_movimientos_manuales(self.user)
        self.assertEqual(ConciliacionMovimientoManual.objects.get().estado, "SUGERIDA")
        self.assertEqual(self.total(), Decimal("10000"))

    def test_confirmacion_antigua_insegura_vuelve_a_revision(self):
        bank = self.banco(descripcion="Ferretería")
        manual = self.manual()
        ConciliacionMovimientoManual.objects.create(movimiento_manual=manual, movimiento_bancario=bank, estado="CONFIRMADA")
        conciliar_movimientos_manuales(self.user)
        self.assertEqual(ConciliacionMovimientoManual.objects.get().estado, "SUGERIDA")
        self.assertEqual(self.total(), Decimal("20000"))

    def test_no_concilia_original_usd_con_clp_del_mismo_equivalente(self):
        self.banco(moneda="USD", monto=Decimal("10"), tipo_cambio_banco=Decimal("1000"))
        self.manual()
        conciliar_movimientos_manuales(self.user)
        self.assertFalse(ConciliacionMovimientoManual.objects.exists())
        self.assertEqual(self.total(), Decimal("20000"))

    def test_confirmacion_usuario_puede_deshacerse_y_no_se_recrea(self):
        bank = self.banco()
        manual = self.manual()
        conciliar_movimientos_manuales(self.user)
        link = ConciliacionMovimientoManual.objects.get()
        self.client.force_login(self.user)
        confirmed = self.client.post(reverse("movimiento-manual-resolver-conciliacion", args=[manual.pk]), {"accion": "confirmar", "conciliacion_id": link.pk})
        self.assertEqual(confirmed.status_code, 200)
        result = self.client.post(reverse("movimiento-manual-resolver-conciliacion", args=[manual.pk]), {"accion": "descartar", "conciliacion_id": link.pk})
        self.assertEqual(result.status_code, 200)
        conciliar_movimientos_manuales(self.user)
        link.refresh_from_db()
        self.assertEqual(link.estado, "DESCARTADA")
        self.assertEqual(self.total(), Decimal("20000"))

    def test_resumen_total_solo_aporta_lo_que_falta_de_la_categoria(self):
        self.banco()
        self.manual(fecha=date(2026, 8, 31), descripcion="Total del mes", monto=Decimal("15000"), monto_clp=Decimal("15000"), es_resumen_mensual=True)
        self.assertEqual(self.total(), Decimal("15000"))
        self.assertEqual(self.total("consumo"), Decimal("15000"))

    def test_resumen_no_genera_ajustes_negativos_ni_cubre_otras_categorias(self):
        self.banco()
        self.manual(es_resumen_mensual=True, monto=Decimal("5000"), monto_clp=Decimal("5000"))
        other_cat = CategoriaTematica.objects.create(user=self.user, nombre="Otros", aplica_a="EGRESO")
        self.banco(categoria=other_cat)
        self.assertEqual(self.total(), Decimal("20000"))

    def test_resumen_adicional_se_suma_de_forma_explicita(self):
        self.banco()
        self.manual(es_resumen_mensual=True, modo_resumen="ADICIONAL")
        self.assertEqual(self.total(), Decimal("20000"))

    def test_resumen_total_cubre_manuales_individuales_e_ingresos_sin_cruzarlos(self):
        self.manual(monto=Decimal("3000"), monto_clp=Decimal("3000"))
        self.manual(es_resumen_mensual=True)
        income_cat = CategoriaTematica.objects.create(user=self.user, nombre="Ingresos", aplica_a="INGRESO")
        self.banco(categoria=income_cat, monto=Decimal("-10000"), monto_clp=Decimal("-10000"))
        self.assertEqual(self.total(), Decimal("10000"))

    def test_conciliacion_confirmada_respeta_vencimiento_y_suma_entre_periodos(self):
        self.product.tipo = "TDC"
        self.product.save()
        bank = self.banco()
        manual = self.manual()
        document = ImportacionEstadoCuenta.objects.create(user=self.user, producto=self.product, nombre_archivo="septiembre.pdf", hash_archivo="septiembre", fecha_vencimiento=date(2026, 9, 5), monto_facturado=Decimal("10000"))
        document.movimientos.add(bank)
        ConciliacionMovimientoManual.objects.create(movimiento_manual=manual, movimiento_bancario=bank, estado="CONFIRMADA", confirmada_manual=True)
        august = self.total()
        september = resumen_gastos(self.user, date(2026, 9, 1), date(2026, 9, 1), "caja")["totales"]["egresos_clp"]
        combined = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 9, 1), "caja")["totales"]["egresos_clp"]
        self.assertEqual(august, Decimal("0"))
        self.assertEqual(september, Decimal("10000"))
        self.assertEqual(combined, august + september)

    def test_migracion_libera_confirmaciones_automaticas_y_preserva_las_explicitas(self):
        import importlib
        from django.apps import apps

        automatic = ConciliacionMovimientoManual.objects.create(movimiento_manual=self.manual(), movimiento_bancario=self.banco(), estado="CONFIRMADA")
        explicit = ConciliacionMovimientoManual.objects.create(movimiento_manual=self.manual(fecha=date(2026, 8, 11)), movimiento_bancario=self.banco(fecha=date(2026, 8, 11)), estado="CONFIRMADA", confirmada_manual=True)
        migration = importlib.import_module("gastos.migrations.0012_movimientomanual_modo_resumen")
        migration.revisar_confirmaciones_automaticas(apps, None)
        automatic.refresh_from_db()
        explicit.refresh_from_db()
        self.assertEqual(automatic.estado, "SUGERIDA")
        self.assertEqual(explicit.estado, "CONFIRMADA")
        self.assertTrue(explicit.confirmada_manual)
