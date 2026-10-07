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
    ConfiguracionBanca,
    EventoFinancieroExterno,
    ImportacionEstadoCuenta,
    LiquidacionHoum,
    MovimientoBancario,
    VinculoPropiedadExterna,
)
from configuracion.models import Banco, Producto, TipoCambio
from departamentos.models import Departamento
from gastos.models import ConciliacionMovimientoManual, GastoProgramado, MovimientoManual
from gastos.conciliaciones import conciliar_movimientos_manuales
from gastos.services import resumen_gastos
from banca.services.presupuesto import proyeccion_no_facturados
from patrimonio.models import AbonoCuentaPorCobrar, CuentaPorCobrar, DetalleCuentaPorCobrar
from prestamos.models import Prestamo


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
        self.assertEqual(
            difference["movimientos"][0]["calculo"],
            "TDC: ajuste automático para llegar al monto facturado",
        )
        self.client.force_login(self.user)
        api = self.client.get(reverse("gastos_resumen_api"), {
            "desde": "2026-09", "hasta": "2026-09"
        })
        self.assertEqual(api.json()["perspectiva"], "caja")
        self.assertEqual(Decimal(api.json()["totales"]["egresos_clp"]), Decimal("100000"))

    def test_detalle_explica_el_papel_de_una_devolucion_en_el_facturado(self):
        statement = self.statement(
            hash_archivo="refund-1", fecha_vencimiento=date(2026, 9, 5),
            monto_facturado=Decimal("800"),
        )
        charge = MovimientoBancario.objects.create(
            user=self.user, producto=self.card, fecha=date(2026, 8, 2),
            descripcion="Compra", monto=Decimal("1000"), monto_clp=Decimal("1000"),
            estado="FACTURADO", origen="PDF", categoria=self.category, hash_externo="refund-charge",
        )
        refund = MovimientoBancario.objects.create(
            user=self.user, producto=self.card, fecha=date(2026, 8, 3),
            descripcion="Devolución", monto=Decimal("-200"), monto_clp=Decimal("-200"),
            estado="FACTURADO", origen="PDF", categoria=self.category, hash_externo="refund-credit",
        )
        statement.movimientos.add(charge, refund)

        result = resumen_gastos(self.user, date(2026, 9, 1), date(2026, 9, 1), "caja")
        lineas = [item for grupo in result["categorias"] for item in grupo["movimientos"]]

        self.assertEqual(result["totales"]["egresos_clp"], Decimal("800"))
        self.assertEqual(
            next(item for item in lineas if item["descripcion"] == "Devolución")["calculo"],
            "TDC: devolución/abono resta del neto facturado",
        )

    def test_pantalla_no_muestra_alerta_persistente_de_estados_faltantes(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("gastos-table"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Estados pendientes")
        self.assertContains(response, "Consolidado mensual")
        self.assertContains(response, "Salidas facturadas y confirmadas")
        self.assertContains(response, 'perspectiva:\'caja\'', html=False)

    def test_panel_agrupa_la_direccion_y_no_repite_egreso_en_cada_categoria(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("gastos-table"))
        contenido = response.content.decode()

        self.assertContains(response, "Cartolas y vencimientos")
        self.assertContains(response, "Salidas por producto")
        self.assertNotIn("Distribución de gastos por categoría", contenido)

    def test_panel_permite_abrir_ingresos_egresos_y_ordenar_por_producto(self):
        MovimientoBancario.objects.create(
            user=self.user, producto=self.card, fecha=date(2026, 8, 10),
            descripcion="Compra a clasificar", monto=Decimal("1000"),
            monto_clp=Decimal("1000"), estado="FACTURADO", origen="PDF",
            hash_externo="vista-categorias",
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("gastos_movimientos"), {
            "fecha_desde": "2026-08-01", "fecha_hasta": "2026-08-31"
        })
        contenido = response.content.decode()

        self.assertContains(response, "Movimientos y categorías")
        self.assertContains(response, "Clasificar pendientes con IA")
        self.assertContains(response, "Reclasificar")
        self.assertContains(response, "Presupuesto aún sin configurar")

    def test_detalle_filtra_por_direccion_y_aisla_usuario(self):
        other_card = Producto.objects.create(user=self.other, banco=self.bank, nombre="Ajena", tipo="TDC")
        account = Producto.objects.create(user=self.user, banco=self.bank, nombre="Cuenta propia", tipo="CUENTA")
        propio = MovimientoBancario.objects.create(
            user=self.user, producto=self.card, fecha=date(2026, 8, 10),
            descripcion="Compra propia", monto=Decimal("12000"), monto_clp=Decimal("12000"),
            categoria=self.category, estado="FACTURADO", origen="PDF", hash_externo="detalle-propio",
        )
        MovimientoBancario.objects.create(
            user=self.user, producto=account, fecha=date(2026, 8, 11),
            descripcion="Ingreso propio", monto=Decimal("-50000"), monto_clp=Decimal("-50000"),
            estado="FACTURADO", origen="PDF", hash_externo="detalle-ingreso",
        )
        MovimientoBancario.objects.create(
            user=self.other, producto=other_card, fecha=date(2026, 8, 10),
            descripcion="Movimiento ajeno", monto=Decimal("90000"), monto_clp=Decimal("90000"),
            estado="FACTURADO", origen="PDF", hash_externo="detalle-ajeno",
        )

        self.client.force_login(self.user)
        response = self.client.get(
            reverse("gastos_detalle_api"),
            {"desde": "2026-08", "hasta": "2026-08", "direccion": "EGRESO", "perspectiva": "consumo"},
        )

        self.assertEqual(response.status_code, 200)
        movimientos = response.json()["movimientos"]
        self.assertEqual([item["descripcion"] for item in movimientos], [propio.descripcion])
        self.assertTrue(all(item["direccion"] == "EGRESO" for item in movimientos))

    def test_consolidado_solo_incluye_cargos_confirmados_de_cuenta(self):
        cuenta = Producto.objects.create(
            user=self.user, banco=self.bank, nombre="Cuenta gastos", tipo="CUENTA"
        )
        for indice, (estado, monto) in enumerate((
            ("FACTURADO", "1000"), ("NO_FACTURADO", "2000"), ("PROVISIONAL", "3000")
        )):
            MovimientoBancario.objects.create(
                user=self.user, producto=cuenta, fecha=date(2026, 8, 10),
                descripcion=f"Cargo {estado}", monto=Decimal(monto), monto_clp=Decimal(monto),
                estado=estado, hash_externo=f"cuenta-estado-{indice}",
            )
        otro_producto = Producto.objects.create(
            user=self.other, banco=self.bank, nombre="Cuenta ajena", tipo="CUENTA"
        )
        MovimientoBancario.objects.create(
            user=self.other, producto=otro_producto, fecha=date(2026, 8, 10),
            descripcion="Cargo ajeno", monto=Decimal("9000"), monto_clp=Decimal("9000"),
            estado="FACTURADO", hash_externo="cargo-ajeno",
        )

        caja = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "caja")
        self.assertEqual(caja["totales"]["egresos_clp"], Decimal("1000"))
        self.assertEqual([m["descripcion"] for g in caja["categorias"] for m in g["movimientos"]],
                         ["Cargo FACTURADO"])

    def test_transferencias_de_cuenta_no_inflan_caja_y_reintegro_bicicleta_si_entra(self):
        cuenta = Producto.objects.create(
            user=self.user, banco=self.bank, nombre="Cuenta transferencias", tipo="CUENTA"
        )
        estado = self.statement(
            hash_archivo="transferencias-cuenta", producto=cuenta,
            tipo_producto="CUENTA", fecha_estado=date(2026, 8, 31),
            fecha_vencimiento=None, monto_facturado=None,
        )
        bicicleta = Prestamo.objects.create(
            user=self.user, nombre="Bicicleta", tipo="TERCEROS", monto_total=Decimal("700")
        )
        for indice, (monto, naturaleza, prestamo) in enumerate((
            ("1000", "OPERACIONAL", None),
            ("5000", "TRANSFERENCIA", None),
            ("-6000", "TRANSFERENCIA", None),
            ("-700", "TRANSFERENCIA", bicicleta),
        )):
            movimiento = MovimientoBancario.objects.create(
                user=self.user, producto=cuenta, fecha=date(2026, 8, 10),
                descripcion=f"Flujo {indice}", monto=Decimal(monto), monto_clp=Decimal(monto),
                naturaleza=naturaleza, prestamo=prestamo, estado="FACTURADO",
                hash_externo=f"cuenta-transferencia-{indice}",
            )
            estado.movimientos.add(movimiento)

        caja = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "caja")
        self.assertEqual(caja["totales"]["egresos_clp"], Decimal("1000"))
        self.assertEqual(caja["totales"]["ingresos_clp"], Decimal("700"))
        self.assertTrue(any(g["categoria"] == "Recuperación bicicleta" for g in caja["categorias"]))

    def test_neat_desglosa_movimientos_pero_no_suma_en_consolidado(self):
        estado = self.statement(hash_archivo="estado-neat", monto_facturado=Decimal("1000"))
        movimiento = MovimientoBancario.objects.create(
            user=self.user, producto=self.card, fecha=date(2026, 8, 10),
            descripcion="NEAT PAGOS", monto=Decimal("1000"), monto_clp=Decimal("1000"),
            estado="FACTURADO", origen="PDF", hash_externo="cargo-neat-unico",
        )
        estado.movimientos.add(movimiento)
        evento = EventoFinancieroExterno.objects.create(
            user=self.user, fuente="neat", tipo="PAGO", external_id="pago-neat-unico",
            estado="CONFIRMADO", fecha_evento=date(2026, 8, 10), monto_neto=Decimal("800"),
        )
        ConciliacionEventoExterno.objects.create(
            evento=evento, movimiento=movimiento, rol="CARGO", monto_asignado=Decimal("800"),
            confianza=Decimal("1"), metodo="ID",
        )

        caja = resumen_gastos(self.user, date(2026, 9, 1), date(2026, 9, 1), "caja")
        lineas = [m for g in caja["categorias"] for m in g["movimientos"]]
        self.assertEqual(caja["totales"]["egresos_clp"], Decimal("1000"))
        self.assertEqual([m["fuente"] for m in lineas], ["TDC"])
        consumo = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "consumo",
                                {"estado": "FACTURADO"})
        self.assertEqual(consumo["totales"]["egresos_clp"], Decimal("1000"))
        self.assertEqual(sum((g["total_clp"] for g in consumo["categorias"]), Decimal("0")),
                         Decimal("1000"))
        self.assertTrue(any(m["fuente"] == "NEAT" for g in consumo["categorias"]
                            for m in g["movimientos"]))

    def test_proyeccion_presupuesto_se_actualiza_al_facturar_y_aisla_usuario(self):
        ConfiguracionBanca.objects.create(
            user=self.user, presupuesto_mensual_clp=Decimal("600"),
        )
        movimientos = []
        for indice, (estado, monto) in enumerate((
            ("NO_FACTURADO", "500"), ("PROVISIONAL", "200"), ("FACTURADO", "900")
        )):
            movimientos.append(MovimientoBancario.objects.create(
                user=self.user, producto=self.card, fecha=date(2026, 8, 10),
                descripcion=f"Compra {indice}", monto=Decimal(monto), monto_clp=Decimal(monto),
                estado=estado, hash_externo=f"proyeccion-{indice}",
            ))
        ajena = Producto.objects.create(user=self.other, banco=self.bank, nombre="TDC ajena", tipo="TDC")
        MovimientoBancario.objects.create(
            user=self.other, producto=ajena, fecha=date(2026, 8, 10),
            descripcion="Compra ajena", monto=Decimal("9000"), monto_clp=Decimal("9000"),
            estado="NO_FACTURADO", hash_externo="proyeccion-ajena",
        )
        MovimientoBancario.objects.create(
            user=self.user, producto=self.card, fecha=date(2026, 7, 10),
            descripcion="Compra anterior sin cartola", monto=Decimal("400"),
            monto_clp=Decimal("400"), estado="NO_FACTURADO", hash_externo="proyeccion-atrasada",
        )
        self.statement(hash_archivo="proyeccion-facturada", monto_facturado=Decimal("300"))
        proyeccion = proyeccion_no_facturados(self.user, 2026, 8)
        self.assertEqual(proyeccion["total_clp"], Decimal("700"))
        self.assertEqual(proyeccion["facturado_clp"], Decimal("300"))
        self.assertEqual(proyeccion["total_proyectado_clp"], Decimal("1000"))
        self.assertEqual(proyeccion["atrasados_cantidad"], 1)
        self.assertEqual(proyeccion["atrasados_clp"], Decimal("400"))
        self.assertTrue(proyeccion["alerta"])
        self.assertEqual(proyeccion["periodo_estimado"], date(2026, 9, 1))
        movimientos[0].estado = "FACTURADO"
        movimientos[0].save(update_fields=["estado"])
        self.assertEqual(proyeccion_no_facturados(self.user, 2026, 8)["total_clp"], Decimal("200"))
        self.assertEqual(proyeccion_no_facturados(self.user, 2026, 8)["total_proyectado_clp"], Decimal("500"))

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
            {"desde": "2026-08", "hasta": "2026-08", "perspectiva": "consumo"},
        )
        self.assertEqual(api.status_code, 200)
        self.assertEqual(api.json()["perspectiva"], "consumo")
        self.assertEqual(api.json()["totales"]["egresos_clp"], "70000.00")

    def test_totales_no_cambian_al_filtrar_las_lineas_visibles(self):
        cuenta = Producto.objects.create(
            user=self.user, banco=self.bank, nombre="Cuenta para filtros", tipo="CUENTA"
        )
        ingreso_category = CategoriaTematica.objects.create(
            user=self.user, nombre="Sueldo", aplica_a="INGRESO", origen="USUARIO"
        )
        MovimientoBancario.objects.create(
            user=self.user, producto=cuenta, fecha=date(2026, 8, 3),
            descripcion="Sueldo", monto=Decimal("-50000"), monto_clp=Decimal("-50000"),
            estado="FACTURADO", origen="PDF", categoria=ingreso_category,
            hash_externo="filtro-ingreso",
        )
        MovimientoBancario.objects.create(
            user=self.user, producto=cuenta, fecha=date(2026, 8, 4),
            descripcion="Compra", monto=Decimal("12000"), monto_clp=Decimal("12000"),
            estado="FACTURADO", origen="PDF", categoria=self.category,
            hash_externo="filtro-egreso",
        )

        completo = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "consumo")
        filtrado = resumen_gastos(
            self.user, date(2026, 8, 1), date(2026, 8, 1), "consumo",
            {"direccion": "EGRESO"},
        )

        self.assertEqual(filtrado["totales"], completo["totales"])
        self.assertEqual(filtrado["cantidad"], 1)
        self.assertEqual(
            [item["descripcion"] for grupo in filtrado["categorias"] for item in grupo["movimientos"]],
            ["Compra"],
        )

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
        pendiente = MovimientoBancario.objects.create(
            user=self.user, producto=cuenta, fecha=date(2026, 8, 30),
            descripcion="Cargo aún no confirmado", monto=Decimal("50000"),
            monto_clp=Decimal("50000"), estado="NO_FACTURADO", origen="CONECTOR",
            hash_externo="scotia-pendiente-caja",
        )
        estado.movimientos.add(pendiente)

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

    def test_movimiento_manual_y_bancario_unico_se_cuentan_una_vez(self):
        fecha = date(2026, 8, 10)
        movimiento = MovimientoBancario.objects.create(
            user=self.user, producto=self.card, fecha=fecha, descripcion="Compra supermercado",
            monto=Decimal("50000"), monto_clp=Decimal("50000"), categoria=self.category,
            estado="FACTURADO", origen="PDF", hash_externo="manual-banco-unico",
        )
        manual = MovimientoManual.objects.create(
            user=self.user, fecha=fecha, periodo=date(2026, 8, 1), direccion="EGRESO",
            monto=Decimal("50000"), monto_clp=Decimal("50000"), categoria=self.category,
            descripcion="Compra supermercado",
        )

        resultado = conciliar_movimientos_manuales(self.user)
        enlace = ConciliacionMovimientoManual.objects.get(movimiento_manual=manual)
        self.assertEqual(resultado["confirmadas"], 0)
        self.assertEqual(enlace.estado, "SUGERIDA")
        self.client.force_login(self.user)
        response = self.client.post(reverse("movimiento-manual-resolver-conciliacion", args=[manual.pk]), {"accion": "confirmar", "conciliacion_id": enlace.pk})
        self.assertEqual(response.status_code, 200)
        enlace.refresh_from_db()
        resumen = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "consumo")
        self.assertEqual(enlace.estado, "CONFIRMADA")
        self.assertEqual(enlace.movimiento_bancario_id, movimiento.pk)
        self.assertEqual(resumen["totales"]["egresos_clp"], Decimal("50000"))

    def test_coincidencias_ambiguas_se_dejan_para_revision(self):
        fecha = date(2026, 8, 11)
        for indice in range(2):
            MovimientoBancario.objects.create(
                user=self.user, producto=self.card, fecha=fecha,
                descripcion="Compra repetida", monto=Decimal("25000"),
                monto_clp=Decimal("25000"), categoria=self.category,
                estado="FACTURADO", origen="PDF", hash_externo=f"manual-banco-ambigua-{indice}",
            )
        manual = MovimientoManual.objects.create(
            user=self.user, fecha=fecha, periodo=date(2026, 8, 1), direccion="EGRESO",
            monto=Decimal("25000"), monto_clp=Decimal("25000"), categoria=self.category,
            descripcion="Compra repetida",
        )

        conciliar_movimientos_manuales(self.user)

        propuestas = ConciliacionMovimientoManual.objects.filter(
            movimiento_manual=manual, estado="SUGERIDA"
        )
        self.assertEqual(propuestas.count(), 2)
        self.assertFalse(ConciliacionMovimientoManual.objects.filter(
            movimiento_manual=manual, estado="CONFIRMADA"
        ).exists())

    def test_confirmacion_manual_se_conserva_despues_de_reimportar(self):
        fecha = date(2026, 8, 12)
        primera = MovimientoBancario.objects.create(
            user=self.user, producto=self.card, fecha=fecha, descripcion="Pago",
            monto=Decimal("30000"), monto_clp=Decimal("30000"), categoria=self.category,
            estado="FACTURADO", origen="PDF", hash_externo="manual-confirmar-1",
        )
        segunda = MovimientoBancario.objects.create(
            user=self.user, producto=self.card, fecha=fecha, descripcion="Pago",
            monto=Decimal("30000"), monto_clp=Decimal("30000"), categoria=self.category,
            estado="FACTURADO", origen="PDF", hash_externo="manual-confirmar-2",
        )
        manual = MovimientoManual.objects.create(
            user=self.user, fecha=fecha, periodo=date(2026, 8, 1), direccion="EGRESO",
            monto=Decimal("30000"), monto_clp=Decimal("30000"), categoria=self.category,
            descripcion="Pago",
        )
        conciliar_movimientos_manuales(self.user)
        enlace = ConciliacionMovimientoManual.objects.get(
            movimiento_manual=manual, movimiento_bancario=primera
        )
        enlace.estado = "CONFIRMADA"
        enlace.confirmada_manual = True
        enlace.save()

        conciliar_movimientos_manuales(self.user)

        enlace.refresh_from_db()
        self.assertEqual(enlace.estado, "CONFIRMADA")
        self.assertTrue(enlace.confirmada_manual)
        self.assertEqual(enlace.movimiento_bancario_id, primera.pk)
        self.assertEqual(ConciliacionMovimientoManual.objects.get(
            movimiento_manual=manual, movimiento_bancario=segunda
        ).estado, "OBSOLETA")

    def test_coincidencia_que_deja_de_ser_valida_se_retira_de_revision(self):
        fecha = date(2026, 8, 12)
        movimiento = MovimientoBancario.objects.create(
            user=self.user, producto=self.card, fecha=fecha, descripcion="Pago",
            monto=Decimal("30000"), monto_clp=Decimal("30000"), categoria=self.category,
            estado="FACTURADO", origen="PDF", hash_externo="manual-obsoleta",
        )
        manual = MovimientoManual.objects.create(
            user=self.user, fecha=fecha, periodo=date(2026, 8, 1), direccion="EGRESO",
            monto=Decimal("30000"), monto_clp=Decimal("30000"), categoria=self.category,
            descripcion="Pago",
        )
        conciliar_movimientos_manuales(self.user)
        movimiento.por_revisar = True
        movimiento.save(update_fields=["por_revisar", "updated_at"])

        conciliar_movimientos_manuales(self.user)

        enlace = ConciliacionMovimientoManual.objects.get(
            movimiento_manual=manual, movimiento_bancario=movimiento
        )
        self.assertEqual(enlace.estado, "OBSOLETA")

    def test_api_lista_y_confirma_solo_conciliaciones_del_usuario(self):
        fecha = date(2026, 8, 13)
        own_movement = MovimientoBancario.objects.create(
            user=self.user, producto=self.card, fecha=fecha, descripcion="Pago propio",
            monto=Decimal("12000"), monto_clp=Decimal("12000"),
            estado="FACTURADO", origen="PDF", hash_externo="api-match-own",
        )
        MovimientoBancario.objects.create(
            user=self.user, producto=self.card, fecha=fecha, descripcion="Pago propio duplicado",
            monto=Decimal("12000"), monto_clp=Decimal("12000"),
            estado="FACTURADO", origen="PDF", hash_externo="api-match-own-ambiguous",
        )
        own_manual = MovimientoManual.objects.create(
            user=self.user, fecha=fecha, periodo=date(2026, 8, 1), direccion="EGRESO",
            monto=Decimal("12000"), monto_clp=Decimal("12000"), categoria=self.category,
            descripcion="Pago propio",
        )
        other_card = Producto.objects.create(
            user=self.other, banco=self.bank, nombre="Tarjeta ajena", tipo="TDC"
        )
        other_category = CategoriaTematica.objects.create(
            user=self.other, nombre="Gasto ajeno", aplica_a="EGRESO"
        )
        other_movement = MovimientoBancario.objects.create(
            user=self.other, producto=other_card, fecha=fecha, descripcion="Pago ajeno",
            monto=Decimal("12000"), monto_clp=Decimal("12000"),
            estado="FACTURADO", origen="PDF", hash_externo="api-match-other",
        )
        other_manual = MovimientoManual.objects.create(
            user=self.other, fecha=fecha, periodo=date(2026, 8, 1), direccion="EGRESO",
            monto=Decimal("12000"), monto_clp=Decimal("12000"), categoria=other_category,
            descripcion="Pago ajeno",
        )
        conciliar_movimientos_manuales(self.user)
        conciliar_movimientos_manuales(self.other)
        own_proposal = ConciliacionMovimientoManual.objects.get(
            movimiento_manual=own_manual, movimiento_bancario=own_movement
        )
        ConciliacionMovimientoManual.objects.get(movimiento_manual=other_manual)
        self.client.force_login(self.user)

        listed = self.client.get(reverse("movimiento-manual-conciliaciones-pendientes"))
        confirmed = self.client.post(
            reverse("movimiento-manual-resolver-conciliacion", args=[own_manual.pk]),
            {"accion": "confirmar", "conciliacion_id": own_proposal.pk},
        )
        denied = self.client.post(
            reverse("movimiento-manual-resolver-conciliacion", args=[other_manual.pk]),
            {"accion": "confirmar", "conciliacion_id": own_proposal.pk},
        )

        own_proposal.refresh_from_db()
        self.assertEqual(listed.status_code, 200)
        self.assertEqual({row["manual_id"] for row in listed.json()}, {own_manual.pk})
        self.assertEqual(len(listed.json()), 2)
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(own_proposal.estado, "CONFIRMADA")
        self.assertTrue(own_proposal.confirmada_manual)
        self.assertEqual(denied.status_code, 404)

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

    def crear_liquidacion_houm(self, *, external_id, fecha_confirmacion, periodo):
        departamento = Departamento.objects.create(
            user=self.user, codigo=external_id[-3:], piso="1",
            metros_cuadrados=Decimal("40"), valor_compra_uf=Decimal("1"),
            valor_actual_uf=Decimal("1"),
        )
        vinculo = VinculoPropiedadExterna.objects.create(
            user=self.user, external_property_id=external_id,
            nombre_externo=departamento.codigo, departamento=departamento,
        )
        evento = EventoFinancieroExterno.objects.create(
            user=self.user, fuente="houm", tipo="LIQUIDACION",
            external_id=external_id, estado="CONFIRMADO", periodo=periodo,
            fecha_evento=periodo, fecha_confirmacion=fecha_confirmacion,
            monto_neto=Decimal("90000"),
        )
        LiquidacionHoum.objects.create(
            evento=evento, vinculo_propiedad=vinculo, external_property_id=external_id,
            propiedad_nombre=departamento.codigo, deposito_neto=Decimal("90000"),
        )
        return evento

    def test_houm_con_deposito_conciliado_usa_solo_la_fecha_bancaria(self):
        periodo_arriendo = date(2026, 9, 1)
        fecha_deposito = date(2026, 8, 20)
        evento = self.crear_liquidacion_houm(
            external_id="houm-deposito", fecha_confirmacion=fecha_deposito,
            periodo=periodo_arriendo,
        )
        cuenta = Producto.objects.create(
            user=self.user, banco=self.bank, nombre="Cuenta corriente", tipo="CUENTA"
        )
        movimiento = MovimientoBancario.objects.create(
            user=self.user, producto=cuenta, fecha=fecha_deposito,
            descripcion="Deposito Houm", monto=Decimal("-90000"),
            monto_clp=Decimal("-90000"), estado="FACTURADO", origen="PDF",
            naturaleza="TRANSFERENCIA",
            hash_externo="houm-deposito-banco",
        )
        estado = self.statement(
            hash_archivo="estado-cuenta-houm", producto=cuenta,
            tipo_producto="CUENTA", fecha_estado=date(2026, 8, 31),
            fecha_vencimiento=None, monto_facturado=None,
        )
        estado.movimientos.add(movimiento)
        ConciliacionEventoExterno.objects.create(
            evento=evento, movimiento=movimiento, rol="DEPOSITO",
            monto_asignado=Decimal("90000"), confianza=Decimal("1"), metodo="ID",
        )

        caja_agosto = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "caja")
        caja_septiembre = resumen_gastos(self.user, date(2026, 9, 1), date(2026, 9, 1), "caja")

        self.assertEqual(caja_agosto["totales"]["ingresos_clp"], Decimal("90000"))
        self.assertEqual(caja_septiembre["totales"]["ingresos_clp"], Decimal("0"))

    def test_houm_sin_deposito_usa_fecha_de_confirmacion_y_no_periodo(self):
        self.crear_liquidacion_houm(
            external_id="houm-pendiente", fecha_confirmacion=date(2026, 8, 20),
            periodo=date(2026, 9, 1),
        )

        agosto = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "caja")
        septiembre = resumen_gastos(self.user, date(2026, 9, 1), date(2026, 9, 1), "caja")

        self.assertEqual(agosto["totales"]["ingresos_clp"], Decimal("90000"))
        self.assertEqual(septiembre["totales"]["ingresos_clp"], Decimal("0"))
