from dataclasses import replace
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from bank_sync.models import MovimientoNormalizado
from banca.models import CategoriaTematica, ImportacionEstadoCuenta, MovimientoBancario, SaldoSnapshot
from banca.services.ingesta import ingestar_movimientos, _resolver_producto
from configuracion.models import Banco, Producto
from configuracion.services.productos import vincular_producto
from patrimonio.models import Pasivo


class IdentidadIngestaTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("identidad")
        self.other = User.objects.create_user("identidad_otra")
        self.bank = Banco.objects.create(nombre="Banco identidad")
        self.old = Producto.objects.create(user=self.user, banco=self.bank, tipo="TDC", nombre="Anterior", ultimos4="1234")
        self.canonical = Producto.objects.create(user=self.user, banco=self.bank, tipo="TDC", nombre="Canónico", ultimos4="5678")
        self.mov = MovimientoNormalizado(date(2026, 8, 10), "Compra supermercado", Decimal("10000"), "CLP", "NO_FACTURADO", tarjeta_id="1234")

    def ingest(self, movimientos, user=None):
        return ingestar_movimientos(user or self.user, self.bank, movimientos)

    def test_dos_compras_identicas_y_reimportacion_conservan_multiplicidad(self):
        first = self.ingest([self.mov, self.mov])
        second = self.ingest([replace(self.mov, estado="FACTURADO"), replace(self.mov, estado="FACTURADO")])
        self.assertEqual(first["nuevos"], 2)
        self.assertEqual(second["nuevos"], 0)
        self.assertEqual(set(first["ids"]), set(second["ids"]))
        self.assertEqual(MovimientoBancario.objects.filter(estado="FACTURADO").count(), 2)

    def test_importacion_parcial_no_borra_la_segunda_compra(self):
        self.ingest([self.mov, self.mov])
        self.ingest([self.mov])
        self.assertEqual(MovimientoBancario.objects.filter(user=self.user).count(), 2)

    def test_identificador_bancario_conserva_identidad_ante_cambio_de_glosa(self):
        original = replace(self.mov, external_id="operacion-1")
        first = self.ingest([original, replace(original, external_id="operacion-2")])
        second = self.ingest([replace(original, descripcion="Nombre definitivo", estado="FACTURADO")])
        self.assertEqual(second["nuevos"], 0)
        self.assertEqual(second["ids"], [first["ids"][0]])
        self.assertEqual(MovimientoBancario.objects.count(), 2)

    def test_pdf_sin_identificador_complementa_al_conector_con_identificador(self):
        first = self.ingest([replace(self.mov, external_id="a"), replace(self.mov, external_id="b")])
        pdf = ingestar_movimientos(self.user, self.bank, [self.mov, self.mov], origen="PDF")
        again = self.ingest([replace(self.mov, external_id="b", descripcion="Glosa final b"), replace(self.mov, external_id="a", descripcion="Glosa final a")])
        self.assertEqual(pdf["nuevos"], 0)
        self.assertEqual(again["nuevos"], 0)
        self.assertEqual(set(first["ids"]), set(pdf["ids"]))

    def test_identificador_bancario_sigue_reconocido_tras_consolidar(self):
        first = self.ingest([replace(self.mov, external_id="a")])
        vincular_producto(self.user, self.old, self.canonical)
        again = self.ingest([replace(self.mov, external_id="a", tarjeta_id="5678", descripcion="Glosa definitiva")])
        self.assertEqual(again["ids"], first["ids"])
        self.assertEqual(again["nuevos"], 0)

    def test_aislamiento_de_usuario_y_producto(self):
        self.ingest([self.mov, self.mov])
        self.ingest([replace(self.mov, tarjeta_id="5678")])
        self.ingest([self.mov], user=self.other)
        self.assertEqual(MovimientoBancario.objects.filter(user=self.user).count(), 3)
        self.assertEqual(MovimientoBancario.objects.filter(user=self.other).count(), 1)

    def test_vincular_y_reimportar_por_ambos_identificadores_no_duplica(self):
        initial = self.ingest([self.mov, self.mov])
        vincular_producto(self.user, self.old, self.canonical)
        for tarjeta in ("1234", "5678"):
            result = self.ingest([replace(self.mov, tarjeta_id=tarjeta)] * 2)
            self.assertEqual(result["nuevos"], 0)
            self.assertEqual(set(result["ids"]), set(initial["ids"]))
        self.assertEqual(_resolver_producto(self.user, self.bank, "1234"), self.canonical)

    def test_consolidacion_conserva_mayor_multiplicidad_y_documentos(self):
        old_ids = self.ingest([self.mov, self.mov])["ids"]
        canonical_id = self.ingest([replace(self.mov, tarjeta_id="5678")])["ids"][0]
        document = ImportacionEstadoCuenta.objects.create(user=self.user, producto=self.old, nombre_archivo="anterior.pdf", hash_archivo="anterior")
        document.movimientos.add(old_ids[0])
        vincular_producto(self.user, self.old, self.canonical)
        self.assertEqual(MovimientoBancario.objects.filter(producto=self.canonical).count(), 2)
        self.assertEqual(list(document.movimientos.values_list("pk", flat=True)), [canonical_id])

    def test_consolidacion_no_pierde_clasificaciones_manuales_incompatibles(self):
        old_id = self.ingest([self.mov])["ids"][0]
        canonical_id = self.ingest([replace(self.mov, tarjeta_id="5678")])["ids"][0]
        for pk, nombre in ((old_id, "Uno"), (canonical_id, "Dos")):
            cat = CategoriaTematica.objects.create(user=self.user, nombre=nombre)
            MovimientoBancario.objects.filter(pk=pk).update(categoria=cat, categoria_es_manual=True)
        with self.assertRaises(ValueError):
            vincular_producto(self.user, self.old, self.canonical)
        self.assertEqual(MovimientoBancario.objects.filter(producto=self.old).count(), 1)

    def test_pasivos_sincronizados_no_se_suman_y_se_preserva_historial(self):
        old = Pasivo.objects.create(user=self.user, producto=self.old, nombre="Deuda anterior", tipo="TDC", sincronizado_banca=True, monto_original=Decimal("15000"), moneda_original="CLP", monto_clp=Decimal("15000"))
        current = Pasivo.objects.create(user=self.user, producto=self.canonical, nombre="Deuda vigente", tipo="TDC", sincronizado_banca=True, monto_original=Decimal("10000"), moneda_original="CLP", monto_clp=Decimal("10000"))
        vincular_producto(self.user, self.old, self.canonical)
        old.refresh_from_db()
        current.refresh_from_db()
        self.assertFalse(old.activo)
        self.assertIsNone(old.producto_id)
        self.assertEqual(old.monto_original, Decimal("15000"))
        self.assertEqual(Pasivo.objects.filter(activo=True, producto=self.canonical).count(), 1)

    def test_snapshot_cuenta_conserva_saldo_disponible(self):
        self.old.tipo = self.canonical.tipo = "CUENTA"
        self.old.save()
        self.canonical.save()
        SaldoSnapshot.objects.create(user=self.user, producto=self.old, fecha=self.mov.fecha, saldo_disponible_clp=Decimal("12345"))
        canonical = SaldoSnapshot.objects.create(user=self.user, producto=self.canonical, fecha=self.mov.fecha)
        vincular_producto(self.user, self.old, self.canonical)
        canonical.refresh_from_db()
        self.assertEqual(canonical.saldo_disponible_clp, Decimal("12345"))

    def test_no_se_permiten_ciclos_de_productos(self):
        vincular_producto(self.user, self.old, self.canonical)
        with self.assertRaises(ValueError):
            vincular_producto(self.user, self.canonical, self.old)

    def test_consolidacion_de_cartolas_deja_una_version_vigente_por_mes(self):
        for product in (self.old, self.canonical):
            ImportacionEstadoCuenta.objects.create(user=self.user, producto=product, nombre_archivo=f"{product.pk}.pdf", hash_archivo=str(product.pk), fecha_vencimiento=date(2026, 9, 5), monto_facturado=Decimal("10000"))
        vincular_producto(self.user, self.old, self.canonical)
        self.assertEqual(ImportacionEstadoCuenta.objects.filter(producto=self.canonical).count(), 2)
        self.assertEqual(ImportacionEstadoCuenta.objects.filter(producto=self.canonical, vigente=True).count(), 1)
