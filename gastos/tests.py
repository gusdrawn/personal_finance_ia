"""Tests de gastos: cálculo de ahorro mensual de gastos programados (usado en el Calendario)."""

import json
from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from banca.models import (
    CategoriaTematica,
    ConciliacionEventoExterno,
    EventoFinancieroExterno,
    ImportacionEstadoCuenta,
    MovimientoBancario,
)
from configuracion.models import Banco, Producto, TipoCambio
from gastos.models import GastoProgramado, MovimientoManual
from gastos.services import resumen_gastos
from patrimonio.models import AbonoCuentaPorCobrar, CuentaPorCobrar, DetalleCuentaPorCobrar


class AhorroMensualTest(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="gasto_test")

    def _gp(self, frecuencia, monto="1200"):
        return GastoProgramado(
            user=self.user,
            nombre="Seguro",
            monto=Decimal(monto),
            fecha_inicio=date(2026, 1, 1),
            frecuencia=frecuencia,
        )

    def test_ahorro_mensual_por_frecuencia(self):
        casos = {
            "MENSUAL": Decimal("1200"),
            "BIMESTRAL": Decimal("600"),
            "TRIMESTRAL": Decimal("400"),
            "SEMESTRAL": Decimal("200"),
            "ANUAL": Decimal("100"),
        }
        for frecuencia, esperado in casos.items():
            with self.subTest(frecuencia=frecuencia):
                self.assertEqual(self._gp(frecuencia).ahorro_mensual, esperado)


class ResumenGastosTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="gastos_unificados", password="x")
        self.other = User.objects.create_user(username="gastos_otro", password="x")
        self.bank = Banco.objects.create(nombre="Banco Gastos Test")
        self.card = Producto.objects.create(
            user=self.user, banco=self.bank, nombre="Visa ••••1234", tipo="TDC", ultimos4="1234"
        )
        self.category = CategoriaTematica.objects.create(
            user=self.user, nombre="Suscripciones", aplica_a="EGRESO", origen="USUARIO"
        )

    def statement(self, **changes):
        values = {
            "user": self.user, "archivo": SimpleUploadedFile("estado.pdf", b"pdf"),
            "nombre_archivo": "estado.pdf", "hash_archivo": "estado-1", "estado": "PROCESADO",
            "banco": self.bank, "producto": self.card, "tarjeta_ultimos4": "1234",
            "fecha_estado": date(2026, 8, 20), "fecha_vencimiento": date(2026, 9, 5),
            "monto_facturado": Decimal("100000"), "moneda": "CLP", "vigente": True,
        }
        values.update(changes)
        return ImportacionEstadoCuenta.objects.create(**values)

    def test_facturado_se_imputa_al_vencimiento_y_el_detalle_no_duplica(self):
        statement = self.statement()
        movement = MovimientoBancario.objects.create(
            user=self.user, producto=self.card, fecha=date(2026, 8, 2),
            descripcion="Streaming", monto=Decimal("70000"), monto_clp=Decimal("70000"),
            estado="FACTURADO", origen="PDF", categoria=self.category, hash_externo="mov-1",
        )
        movement.importaciones.add(statement)

        august = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "caja")
        september = resumen_gastos(self.user, date(2026, 9, 1), date(2026, 9, 1), "caja")

        self.assertEqual(august["totales"]["egresos_clp"], Decimal("0"))
        self.assertEqual(september["totales"]["egresos_clp"], Decimal("100000"))
        self.assertEqual(len(september["estados_cuenta"]), 1)
        self.assertEqual(september["estados_cuenta"][0]["monto_clp"], Decimal("100000"))
        difference = next(g for g in september["categorias"] if g["categoria"] == "Diferencia con facturado")
        self.assertEqual(difference["total_clp"], Decimal("30000"))

    def test_pantalla_no_muestra_alerta_persistente_de_estados_faltantes(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("gastos-table"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Estados pendientes")
        self.assertContains(response, "Distribución de gastos por categoría")
        self.assertContains(response, '<option value="consumo" selected>', html=False)

    def test_panel_agrupa_la_direccion_y_no_repite_egreso_en_cada_categoria(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("gastos-table"))
        contenido = response.content.decode()

        self.assertContains(response, "Categoría → fuente/producto → movimiento")
        self.assertIn("function agruparPorDireccion", contenido)
        self.assertIn("direccionLabels={EGRESO:'Egresos',INGRESO:'Ingresos'}", contenido)
        self.assertNotIn("<b>${esc(g.direccion)}</b>", contenido)

    def test_consumo_usa_compra_y_excluye_conversion_a_activo(self):
        statement = self.statement()
        movement = MovimientoBancario.objects.create(
            user=self.user, producto=self.card, fecha=date(2026, 8, 2), descripcion="Streaming",
            monto=Decimal("70000"), monto_clp=Decimal("70000"), categoria=self.category,
            estado="FACTURADO", origen="PDF", hash_externo="mov-consumo",
        )
        movement.importaciones.add(statement)
        MovimientoBancario.objects.create(
            user=self.user, producto=self.card, fecha=date(2026, 8, 3), descripcion="Inversión",
            monto=Decimal("50000"), monto_clp=Decimal("50000"), naturaleza="TRANSFERENCIA",
            estado="FACTURADO", origen="PDF", hash_externo="mov-activo",
        )
        result = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "consumo")
        self.assertEqual(result["totales"]["egresos_clp"], Decimal("70000"))
        self.assertEqual([row["id"] for row in result["estados_cuenta"]], [statement.pk])
        september = resumen_gastos(self.user, date(2026, 9, 1), date(2026, 9, 1), "consumo")
        self.assertEqual(september["totales"]["egresos_clp"], Decimal("0"))
        self.assertEqual(september["estados_cuenta"], [])

        self.client.force_login(self.user)
        api = self.client.get(
            reverse("gastos_resumen_api"),
            {"desde": "2026-08", "hasta": "2026-08"},
        )
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.json()["perspectiva"], "consumo")
        self.assertEqual(api.json()["totales"]["egresos_clp"], "70000.00")

    def test_cuenta_corriente_separa_abonos_de_cargos_y_no_desglosa_deposito_neat(self):
        cuenta = Producto.objects.create(
            user=self.user, banco=self.bank, nombre="Cuenta corriente", tipo="CUENTA"
        )
        sueldo = MovimientoBancario.objects.create(
            user=self.user, producto=cuenta, fecha=date(2026, 8, 28),
            descripcion="REMUNERACION EMPRESA", monto=Decimal("-4565986"),
            monto_clp=Decimal("-4565986"), estado="FACTURADO", origen="PDF",
            hash_externo="sueldo-cuenta",
        )
        MovimientoBancario.objects.create(
            user=self.user, producto=cuenta, fecha=date(2026, 8, 27),
            descripcion="COM.MANTENCION PLAN", monto=Decimal("27774"),
            monto_clp=Decimal("27774"), estado="FACTURADO", origen="PDF",
            hash_externo="comision-cuenta",
        )
        evento = EventoFinancieroExterno.objects.create(
            user=self.user, fuente="neat", tipo="PAGO", external_id="deposito-neat",
            estado="CONFIRMADO", fecha_evento=date(2026, 8, 27), monto_neto=Decimal("1000"),
        )
        ConciliacionEventoExterno.objects.create(
            evento=evento, movimiento=sueldo, rol="DEPOSITO",
            monto_asignado=Decimal("1000"), confianza=Decimal("0.9"), metodo="MONTO_FECHA",
        )

        result = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "consumo")

        self.assertEqual(result["totales"]["ingresos_clp"], Decimal("4565986"))
        self.assertEqual(result["totales"]["egresos_clp"], Decimal("27774"))
        ingreso = next(g for g in result["categorias"] if g["direccion"] == "INGRESO")
        self.assertEqual(ingreso["movimientos"][0]["fuente"], "CUENTA")
        self.assertEqual(ingreso["movimientos"][0]["descripcion"], "REMUNERACION EMPRESA")

    def test_cuenta_corriente_importada_aparece_en_caja_sin_diferencia_facturada(self):
        cuenta = Producto.objects.create(
            user=self.user, banco=self.bank, nombre="Cuenta corriente Scotiabank", tipo="CUENTA"
        )
        estado = ImportacionEstadoCuenta.objects.create(
            user=self.user,
            archivo=SimpleUploadedFile("scotia.pdf", b"pdf"),
            nombre_archivo="scotia.pdf",
            hash_archivo="scotia-caja",
            estado="PROCESADO",
            banco=self.bank,
            producto=cuenta,
            tipo_producto="CUENTA",
            numero_producto="0-0098-55883-09",
            fecha_estado=date(2026, 8, 31),
            periodo_inicio=date(2026, 8, 1),
            periodo_fin=date(2026, 8, 31),
            saldo_final_clp=Decimal("100000"),
            vigente=True,
        )
        ingreso = MovimientoBancario.objects.create(
            user=self.user, producto=cuenta, fecha=date(2026, 8, 28),
            descripcion="REMUNERACION EMPRESA", monto=Decimal("-4565986"),
            monto_clp=Decimal("-4565986"), estado="FACTURADO", origen="PDF",
            hash_externo="scotia-ingreso-caja",
        )
        cargo = MovimientoBancario.objects.create(
            user=self.user, producto=cuenta, fecha=date(2026, 8, 29),
            descripcion="COMISION MANTENCION PLAN", monto=Decimal("27774"),
            monto_clp=Decimal("27774"), estado="FACTURADO", origen="PDF",
            hash_externo="scotia-cargo-caja",
        )
        estado.movimientos.add(ingreso, cargo)

        caja = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "caja")

        self.assertEqual(caja["totales"]["ingresos_clp"], Decimal("4565986"))
        self.assertEqual(caja["totales"]["egresos_clp"], Decimal("27774"))
        self.assertFalse(any(g["categoria"] == "Diferencia con facturado" for g in caja["categorias"]))
        self.assertEqual(len(caja["estados_cuenta"]), 1)

    def test_cargo_vinculado_a_cuenta_por_cobrar_no_es_consumo(self):
        movement = MovimientoBancario.objects.create(
            user=self.user,
            producto=self.card,
            fecha=date(2026, 8, 5),
            descripcion="Comunidad Feliz",
            monto=Decimal("80697"),
            monto_clp=Decimal("80697"),
            categoria=self.category,
            estado="FACTURADO",
            origen="PDF",
            hash_externo="comunidad-tercero",
        )
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Tercero")
        DetalleCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            descripcion="Comunidad Feliz",
            periodo=date(2026, 8, 1),
            fecha=movement.fecha,
            monto=movement.monto_clp,
            origen="MOVIMIENTO",
            monto_manual=False,
            movimiento=movement,
        )

        consumo = resumen_gastos(
            self.user, date(2026, 8, 1), date(2026, 8, 1), "consumo"
        )
        self.assertEqual(consumo["totales"]["egresos_clp"], Decimal("0"))
        self.assertEqual(consumo["categorias"], [])
        self.assertEqual(cuenta.activo_patrimonial.monto_clp, Decimal("80697"))

        AbonoCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            fecha=date(2026, 8, 20),
            monto=Decimal("30000"),
            descripcion="Reintegro parcial",
        )
        caja = resumen_gastos(
            self.user, date(2026, 8, 1), date(2026, 8, 1), "caja"
        )
        self.assertEqual(caja["totales"]["ingresos_clp"], Decimal("30000"))
        recuperacion = next(
            grupo
            for grupo in caja["categorias"]
            if grupo["categoria"] == "Recuperación cuenta por cobrar"
        )
        self.assertEqual(recuperacion["total_clp"], Decimal("30000"))
        self.assertEqual(recuperacion["movimientos"][0]["naturaleza"], "ACTIVO_RECUPERABLE")
        cuenta.activo_patrimonial.refresh_from_db()
        self.assertEqual(cuenta.activo_patrimonial.monto_clp, Decimal("50697"))

    def test_usd_es_estimado_y_pago_real_reemplaza_sin_perder_estimacion(self):
        TipoCambio.objects.create(fecha=date(2026, 9, 5), dolar=Decimal("950"), uf=Decimal("38000"), fuente="test")
        statement = self.statement(
            hash_archivo="usd-1", moneda="USD", monto_facturado=Decimal("100"),
            monto_facturado_clp_estimado=Decimal("95000"), tipo_cambio_estimado=Decimal("950"),
        )
        estimated = resumen_gastos(self.user, date(2026, 9, 1), date(2026, 9, 1), "caja")
        self.assertEqual(estimated["totales"]["egresos_clp"], Decimal("95000"))
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("gastos_pago_real_api", args=[statement.pk]),
            {"monto_clp": "97250", "fecha_pago": "2026-09-05"}, content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        statement.refresh_from_db()
        self.assertEqual(statement.monto_facturado_clp_estimado, Decimal("95000"))
        actual = resumen_gastos(self.user, date(2026, 9, 1), date(2026, 9, 1), "caja")
        self.assertEqual(actual["totales"]["egresos_clp"], Decimal("97250"))

    def test_estado_ausente_cubre_cero_y_no_expone_otro_usuario(self):
        other_card = Producto.objects.create(user=self.other, banco=self.bank, nombre="Ajena", tipo="TDC")
        result = resumen_gastos(self.user, date(2026, 9, 1), date(2026, 9, 1), "caja")
        self.assertEqual(result["totales"]["egresos_clp"], Decimal("0"))
        self.assertEqual([row["producto_id"] for row in result["cobertura"]], [self.card.pk])
        self.assertNotIn(other_card.pk, [row["producto_id"] for row in result["cobertura"]])

    def test_manual_operacional_es_caja_y_consumo_transferencia_no_es_ninguno(self):
        MovimientoManual.objects.create(
            user=self.user, fecha=date(2026, 8, 31), periodo=date(2026, 8, 1),
            direccion="EGRESO", monto=Decimal("20000"), monto_clp=Decimal("20000"),
            categoria=self.category, naturaleza="OPERACIONAL", descripcion="Compra manual",
        )
        MovimientoManual.objects.create(
            user=self.user, fecha=date(2026, 8, 31), periodo=date(2026, 8, 1),
            direccion="EGRESO", monto=Decimal("90000"), monto_clp=Decimal("90000"),
            categoria=self.category, naturaleza="TRANSFERENCIA_INTERNA", descripcion="Traspaso",
        )
        caja = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "caja")
        consumo = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "consumo")
        self.assertEqual(caja["totales"]["egresos_clp"], Decimal("20000"))
        self.assertEqual(consumo["totales"]["egresos_clp"], Decimal("20000"))

    def test_api_manual_y_carga_mensual_aislan_categoria_por_usuario(self):
        foreign_category = CategoriaTematica.objects.create(
            user=self.other, nombre="Privada", aplica_a="EGRESO"
        )
        self.client.force_login(self.user)
        denied = self.client.post(
            reverse("movimiento-manual-list"),
            {"fecha": "2026-08-03", "direccion": "EGRESO", "monto": "1000",
             "moneda": "CLP", "categoria": foreign_category.pk,
             "naturaleza": "OPERACIONAL", "descripcion": "No permitido"},
        )
        self.assertEqual(denied.status_code, 400)
        created = self.client.post(
            reverse("movimiento-manual-carga-mensual"),
            json.dumps({"periodo": "2026-08", "movimientos": [{
                "categoria": self.category.pk, "direccion": "EGRESO", "monto": "12000"
            }]}), content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        row = MovimientoManual.objects.get(user=self.user, es_resumen_mensual=True)
        self.assertEqual(row.fecha, date(2026, 8, 31))

    def test_fusion_categoria_suma_resumenes_y_archiva_origen(self):
        destination = CategoriaTematica.objects.create(
            user=self.user, nombre="Servicios", aplica_a="EGRESO", origen="USUARIO"
        )
        for category, amount in ((self.category, "1000"), (destination, "2000")):
            MovimientoManual.objects.create(
                user=self.user, fecha=date(2026, 8, 31), periodo=date(2026, 8, 1),
                direccion="EGRESO", monto=Decimal(amount), monto_clp=Decimal(amount),
                categoria=category, descripcion=category.nombre, es_resumen_mensual=True,
            )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("categoria-gasto-fusionar", args=[self.category.pk]),
            json.dumps({"destino": destination.pk}), content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.category.refresh_from_db()
        merged = MovimientoManual.objects.get(user=self.user, categoria=destination)
        self.assertFalse(self.category.activa)
        self.assertEqual(merged.monto_clp, Decimal("3000"))
