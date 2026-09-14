from datetime import date, datetime, time
from decimal import Decimal
from io import BytesIO
from zoneinfo import ZoneInfo
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from bank_sync.exceptions import InteractiveAuthRequired
from bank_sync.models import LiquidacionArriendoNormalizada, PagoExternoNormalizado, ResultadoExtraccionExterna
from banca.models import (
    CategoriaTematica,
    ConciliacionEventoExterno,
    CredencialBanco,
    EventoFinancieroExterno,
    LiquidacionHoum,
    MovimientoBancario,
    VinculoPropiedadExterna,
)
from banca.services.calendario_externo import eventos_externos_calendario
from banca.services.fuentes_externas import (
    conciliar_eventos,
    debe_sincronizar,
    houm_completo,
    importar_archivo_externo,
    ingestar_liquidaciones_houm,
    ingestar_pagos_neat,
    parsear_houm,
    parsear_neat,
    run_external_sync,
)
from banca.services.houm_analytics import resumen_houm_departamentos
from configuracion.models import Banco, Producto, TipoCambio
from departamentos.models import Arrendatario, CreditoHipotecario, Departamento
from gastos.services import resumen_gastos
from patrimonio.models import CuentaPorCobrar, DetalleCuentaPorCobrar


class FuentesExternasTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("externo", password="x")
        self.other = User.objects.create_user("otro-externo", password="x")
        self.neat_bank = Banco.objects.create(nombre="Neat Test")
        self.houm_bank = Banco.objects.create(nombre="Houm Test")
        self.neat = CredencialBanco.objects.create(
            user=self.user, banco=self.neat_bank, conector="neat", tipo_fuente="pagos",
            dia_inicio=1, dia_fin=1, hora_sincronizacion=time(9), sincronizacion_automatica=True,
        )
        self.houm = CredencialBanco.objects.create(
            user=self.user, banco=self.houm_bank, conector="houm",
            tipo_fuente="administracion_inmobiliaria", dia_inicio=2, dia_fin=10,
            hora_sincronizacion=time(9), sincronizacion_automatica=True,
        )
        self.department = Departamento.objects.create(
            user=self.user, codigo="E107", piso="10", metros_cuadrados=Decimal("50"),
            valor_compra_uf=Decimal("3000"), valor_actual_uf=Decimal("3200"), activo=True,
        )

    def test_parser_neat_preserva_principal_comision_y_total(self):
        content = (
            "id_pago;estado;monto;comision;fecha_cargo;cuenta;tarjeta_ultimos4\n"
            "N-1;exitoso;100000;2500;2026-08-01;Servicios;1234\n"
        ).encode()
        payment = parsear_neat(content, "neat.csv")[0]
        self.assertEqual(payment.estado, "CONFIRMADO")
        self.assertEqual(payment.monto_pagado, Decimal("100000"))
        self.assertEqual(payment.comision, Decimal("2500"))
        self.assertEqual(payment.total_cargado, Decimal("102500"))

    def test_parser_neat_reconoce_fechas_del_reporte_y_distingue_meses(self):
        content = (
            "cuenta,monto,comision,monto_total,dia_de_pago,dia_de_deposito,status,alias\n"
            '"ENEL",53641,0,53641,"2026-06-04T03:39:36.000Z","2026-06-04T03:39:36.000Z",Realizado,"Luz 1024"\n'
            '"Entel PCS",25980,289,26269,"2026-09-02T22:14:35.000Z","2026-09-02T22:14:35.000Z",Realizado,"Entel Fabiana"\n'
        ).encode()

        payments = parsear_neat(content, "reporte_neat.csv")

        self.assertEqual(payments[0].monto_pagado, Decimal("53641"))
        self.assertEqual(payments[0].fecha_cargo, date(2026, 6, 4))
        self.assertEqual(payments[0].fecha_transferencia, date(2026, 6, 4))
        self.assertEqual(payments[1].monto_pagado, Decimal("25980"))
        self.assertEqual(payments[1].total_cargado, Decimal("26269"))
        self.assertEqual(payments[1].fecha_cargo, date(2026, 9, 2))
        self.assertEqual(payments[1].fecha_transferencia, date(2026, 9, 2))
        self.assertEqual(payments[0].metadata["alias"], "Luz 1024")

    def test_parser_neat_acepta_correo_de_comprobante(self):
        content = (
            "ID del pago: NP-EMAIL-1 Estado: Exitoso Monto pagado: $100.000 "
            "Comisión: $2.500 Total cargado: $102.500 Fecha de cargo: 01/08/2026 "
            "Destinatario: Proveedor Uno Tarjeta terminada en 1234"
        ).encode()
        payment = parsear_neat(content, "comprobante.eml")[0]
        self.assertEqual(payment.external_id, "NP-EMAIL-1")
        self.assertEqual(payment.estado, "CONFIRMADO")
        self.assertEqual(payment.total_cargado, Decimal("102500"))
        self.assertEqual(payment.tarjeta_ultimos4, "1234")

    def test_parser_excel_houm_desglosa_primer_pago(self):
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["ID liquidación", "ID propiedad", "Propiedad", "Período", "Renta bruta", "Comisión", "IVA", "Corretaje", "Reparaciones", "Promociones", "Depósito neto", "Fecha depósito"])
        sheet.append(["H-1", "P-107", "Departamento E107", "2026-08", 600000, 40000, 7600, 100000, 20000, 15000, 417400, "2026-08-06"])
        output = BytesIO()
        workbook.save(output)
        settlement = parsear_houm(output.getvalue(), "houm.xlsx")[0]
        self.assertEqual(settlement.deposito_neto, Decimal("417400"))
        self.assertEqual(settlement.corretaje, Decimal("100000"))
        self.assertEqual({detail.tipo for detail in settlement.detalles}, {"COMISION", "IVA", "CORRETAJE", "PROMOCION", "REPARACION"})

    def test_parser_reporte_oficial_houm_reconoce_plan_deposito_y_fecha(self):
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "iNFORME DE PAGOS"
        sheet.append([None, "Propietario Demo"])
        sheet.append([None, "Resumen de septiembre 2026"])
        sheet.append([])
        sheet.append(["Detalle de Propiedades"])
        sheet.append([None, "ID Propiedad", "Propiedad", "Arriendo", "Plan Houm", "Ajustes", "Depósito ", "Fecha de pago"])
        sheet.append([None, 116614, "Miraflores 820 departamento 107F", 380000, 35788, 0, 357828, "2026-09-03"])
        output = BytesIO()
        workbook.save(output)

        settlement = parsear_houm(output.getvalue(), "reporte-propiedades-9-2026.xlsx")[0]

        self.assertEqual(settlement.propiedad_id, "116614")
        self.assertEqual(settlement.periodo, date(2026, 9, 1))
        self.assertEqual(settlement.renta_bruta, Decimal("380000"))
        self.assertEqual(settlement.comision, Decimal("35788"))
        self.assertEqual(settlement.deposito_neto, Decimal("357828"))
        self.assertEqual(settlement.fecha_deposito, date(2026, 9, 3))

    def test_reporte_oficial_vincula_unidad_invertida_y_actualiza_el_mismo_mes(self):
        department_f107 = Departamento.objects.create(
            user=self.user, codigo="F107", piso="10", metros_cuadrados=Decimal("50"),
            valor_compra_uf=Decimal("3000"), valor_actual_uf=Decimal("3200"), activo=True,
        )
        first = LiquidacionArriendoNormalizada(
            external_id="archivo-estable", propiedad_id="116614",
            propiedad_nombre="Miraflores 820 departamento 107F",
            periodo=date(2026, 9, 1), renta_bruta=Decimal("380000"),
            deposito_neto=Decimal("340000"), fecha_deposito=date(2026, 9, 3),
            comision=Decimal("35000"),
        )
        second = LiquidacionArriendoNormalizada(
            external_id="archivo-estable", propiedad_id="116614",
            propiedad_nombre="Miraflores 820 departamento 107F",
            periodo=date(2026, 9, 1), renta_bruta=Decimal("380000"),
            deposito_neto=Decimal("345000"), fecha_deposito=date(2026, 9, 4),
            comision=Decimal("35000"),
        )

        ingestar_liquidaciones_houm(self.user, [first], credential=self.houm)
        ingestar_liquidaciones_houm(self.user, [second], credential=self.houm)

        liquidation = LiquidacionHoum.objects.get(evento__external_id="archivo-estable")
        self.assertEqual(liquidation.vinculo_propiedad.departamento, department_f107)
        self.assertEqual(liquidation.deposito_neto, Decimal("345000"))
        self.assertEqual(LiquidacionHoum.objects.filter(evento__user=self.user).count(), 1)

    def test_analitica_houm_muestra_historial_costos_deficit_y_aisla_usuario(self):
        ingestar_liquidaciones_houm(self.user, [
            LiquidacionArriendoNormalizada(
                "H-ago", "P-107", "E107", date(2026, 8, 1), Decimal("600000"),
                Decimal("520000"), fecha_deposito=date(2026, 8, 6),
                comision=Decimal("50000"), reparaciones=Decimal("10000"),
            ),
            LiquidacionArriendoNormalizada(
                "H-sep", "P-107", "E107", date(2026, 9, 1), Decimal("600000"),
                Decimal("500000"), fecha_deposito=date(2026, 9, 6),
                comision=Decimal("50000"), reparaciones=Decimal("20000"),
            ),
        ], credential=self.houm)
        other_department = Departamento.objects.create(
            user=self.other, codigo="E107", piso="10", metros_cuadrados=Decimal("50"),
            valor_compra_uf=Decimal("3000"), valor_actual_uf=Decimal("3200"), activo=True,
        )
        VinculoPropiedadExterna.objects.create(
            user=self.other, external_property_id="OTHER", departamento=other_department,
        )
        ingestar_liquidaciones_houm(self.other, [LiquidacionArriendoNormalizada(
            "H-other", "OTHER", "E107", date(2026, 9, 1), Decimal("999999"),
            Decimal("1"), fecha_deposito=date(2026, 9, 6),
        )])

        summary = resumen_houm_departamentos(self.user)

        self.assertEqual(summary["periodo_actual"], date(2026, 9, 1))
        self.assertEqual(len(summary["historial"]), 2)
        self.assertEqual(summary["actual"]["administracion"], Decimal("50000"))
        self.assertEqual(summary["actual"]["deficit"], Decimal("30000"))
        self.assertEqual(summary["por_departamento"][self.department.id]["meses"], 2)
        self.assertNotIn(Decimal("999999"), [row["renta"] for row in summary["historial"]])

        self.client.login(username="externo", password="x")
        page = self.client.get(reverse("departamentos"))
        self.assertContains(page, "Resultado Houm")
        self.assertContains(page, "Déficit del mes")
        self.assertContains(page, "houmHistoryChart")

    def test_unidad_ausente_del_reporte_registra_vacancia_y_mantiene_dividendo(self):
        reported_department = Departamento.objects.create(
            user=self.user, codigo="F107", piso="10", metros_cuadrados=Decimal("50"),
            valor_compra_uf=Decimal("3000"), valor_actual_uf=Decimal("3200"), activo=True,
        )
        Arrendatario.objects.create(
            departamento=self.department, nombre="Contrato E107", rut="11.111.111-1",
            fecha_inicio_contrato=date(2026, 1, 1), monto_arriendo_clp=Decimal("330000"),
            activo=True,
        )
        CreditoHipotecario.objects.create(
            departamento=self.department, banco=self.houm_bank,
            monto_original_uf=Decimal("2000"), tasa_anual=Decimal("4.5"),
            plazo_anos=20, tipo_tasa="FIJA", cuota_uf=Decimal("8.73"),
            saldo_actual_uf=Decimal("1800"), cuotas_pagadas=12, cuotas_totales=240,
        )
        TipoCambio.objects.create(
            fecha=date(2026, 9, 1), uf=Decimal("40000"), dolar=Decimal("950"),
            fuente="mindicador.cl",
        )
        ingestar_liquidaciones_houm(self.user, [LiquidacionArriendoNormalizada(
            "H-current", "P-F107", "F107", date(2026, 9, 1), Decimal("380000"),
            Decimal("340000"), fecha_deposito=date(2026, 9, 4),
            comision=Decimal("40000"),
        )])

        summary = resumen_houm_departamentos(self.user)
        missing = summary["por_departamento"][self.department.id]["actual"]

        self.assertEqual(missing["estado"], "SIN_INGRESO")
        self.assertTrue(missing["sin_ingreso"])
        self.assertEqual(missing["deposito"], Decimal("0"))
        self.assertEqual(missing["deficit"], Decimal("330000"))
        self.assertEqual(summary["actual"]["deficit"], Decimal("330000"))
        self.assertEqual(summary["sin_ingreso_count"], 1)

        self.client.login(username="externo", password="x")
        page = self.client.get(reverse("departamentos"))
        row = next(item for item in page.context["deptos_con_roi"] if item["depto"] == self.department)
        self.assertEqual(row["ingreso_real"], Decimal("0"))
        self.assertEqual(row["diferencia_dividendo"], Decimal("-349200.00"))
        self.assertContains(page, "Sin ingreso informado · vacante")

    def test_importacion_es_idempotente_y_no_crea_movimientos(self):
        content = b"id_pago,estado,monto,comision,fecha_cargo\nN-2,exitoso,1000,20,2026-08-01\n"
        upload = SimpleUploadedFile("neat.csv", content)
        record, duplicate = importar_archivo_externo(self.user, "neat", upload)
        second, duplicate_second = importar_archivo_externo(
            self.user, "neat", SimpleUploadedFile("neat.csv", content)
        )
        self.assertFalse(duplicate)
        self.assertTrue(duplicate_second)
        self.assertEqual(record.pk, second.pk)
        self.assertEqual(bytes(record.contenido_privado), content)
        self.assertEqual(EventoFinancieroExterno.objects.filter(user=self.user).count(), 1)
        self.assertEqual(MovimientoBancario.objects.count(), 0)
        self.client.login(username="externo", password="x")
        response = self.client.get(reverse("banca_descargar_importacion_externa", args=[record.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, content)

    def test_archivo_privado_no_puede_descargarlo_otro_usuario(self):
        record, _ = importar_archivo_externo(
            self.user, "neat", SimpleUploadedFile("privado.csv", b"id_pago,monto\nN-private,10\n")
        )
        self.client.login(username="otro-externo", password="x")
        response = self.client.get(reverse("banca_descargar_importacion_externa", args=[record.pk]))
        self.assertEqual(response.status_code, 404)

    def test_archivo_malformado_queda_auditado_como_error(self):
        record, duplicate = importar_archivo_externo(
            self.user, "houm", SimpleUploadedFile("houm.txt", b"sin formato")
        )
        self.assertFalse(duplicate)
        self.assertEqual(record.estado, "ERROR")
        self.assertIn("no contiene un monto reconocible", record.mensaje)

    def test_panel_de_fuentes_muestra_zona_de_arrastre_para_archivo_oficial(self):
        self.client.login(username="externo", password="x")

        pagina = self.client.get(reverse("banca_fuentes_externas"))

        self.assertContains(pagina, "data-file-dropzone")
        self.assertContains(pagina, "Arrastra el archivo oficial aquí")
        self.assertContains(pagina, "data-file-input")
        self.assertContains(pagina, "data-file-clear")
        self.assertContains(pagina, ".xlsx,.xlsm,.csv,.json,.eml,.txt,.pdf,.html")
        self.assertContains(pagina, "file-dropzone.js")

    def test_houm_vincula_departamento_y_aporta_neto_una_vez(self):
        settlement = LiquidacionArriendoNormalizada(
            external_id="H-2", propiedad_id="P-107", propiedad_nombre="Depto E107",
            periodo=date(2026, 8, 1), renta_bruta=Decimal("600000"),
            deposito_neto=Decimal("520000"), fecha_deposito=date(2026, 8, 6),
            comision=Decimal("50000"), reparaciones=Decimal("30000"),
        )
        ingestar_liquidaciones_houm(self.user, [settlement], credential=self.houm)
        event = EventoFinancieroExterno.objects.get(external_id="H-2")
        liquidation = event.liquidacion_houm
        self.assertEqual(liquidation.vinculo_propiedad.departamento, self.department)
        resumen = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "caja")
        self.assertEqual(resumen["totales"]["ingresos_clp"], Decimal("520000"))
        self.assertEqual(MovimientoBancario.objects.count(), 0)

    def test_houm_sin_vinculo_queda_pendiente_y_no_contabiliza(self):
        ingestar_liquidaciones_houm(self.user, [LiquidacionArriendoNormalizada(
            external_id="H-conflict", propiedad_id="P-999", propiedad_nombre="Unidad desconocida",
            periodo=date(2026, 8, 1), renta_bruta=Decimal("600000"),
            deposito_neto=Decimal("520000"), fecha_deposito=date(2026, 8, 6),
        )])
        event = EventoFinancieroExterno.objects.get(external_id="H-conflict")
        self.assertEqual(event.estado, "REQUIERE_REVISION")
        resumen = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "caja")
        self.assertEqual(resumen["totales"]["ingresos_clp"], Decimal("0"))

    def test_neat_concilia_cargo_sin_duplicar_principal_y_comision(self):
        product = Producto.objects.create(user=self.user, banco=self.neat_bank, nombre="TDC", tipo="TDC")
        movement = MovimientoBancario.objects.create(
            user=self.user, producto=product, fecha=date(2026, 8, 2), descripcion="NEAT PAGOS",
            monto=Decimal("1020"), monto_clp=Decimal("1020"), hash_externo="neat-charge",
        )
        ingestar_pagos_neat(self.user, [PagoExternoNormalizado(
            external_id="N-3", estado="CONFIRMADO", monto_pagado=Decimal("1000"),
            comision=Decimal("20"), fecha_cargo=date(2026, 8, 1),
        )])
        conciliar_eventos(self.user)
        reconciliation = ConciliacionEventoExterno.objects.get(evento__external_id="N-3")
        self.assertEqual(reconciliation.movimiento, movement)
        self.assertEqual(reconciliation.monto_asignado, Decimal("1020"))
        self.assertEqual(MovimientoBancario.objects.count(), 1)

    def test_neat_reemplaza_categoria_generica_con_detalle_del_destinatario(self):
        product = Producto.objects.create(
            user=self.user, banco=self.neat_bank, nombre="TDC Servicios", tipo="TDC"
        )
        generic = CategoriaTematica.objects.create(
            user=self.user, nombre="Intereses y comisiones", aplica_a="EGRESO", origen="IA"
        )
        movement = MovimientoBancario.objects.create(
            user=self.user, producto=product, fecha=date(2026, 8, 2),
            descripcion="NEAT CUENTAS BASICAS", monto=Decimal("25580"),
            monto_clp=Decimal("25580"), categoria=generic, fuente_clasificacion="IA",
            estado="FACTURADO", hash_externo="neat-aguas",
        )
        ingestar_pagos_neat(self.user, [PagoExternoNormalizado(
            external_id="N-AGUAS", estado="CONFIRMADO", monto_pagado=Decimal("25580"),
            cuenta="Aguas Andinas", fecha_cargo=date(2026, 8, 2),
        )])

        resultado = conciliar_eventos(self.user)

        movement.refresh_from_db()
        event = EventoFinancieroExterno.objects.get(external_id="N-AGUAS")
        self.assertGreaterEqual(resultado["clasificados"], 1)
        self.assertEqual(event.categoria.nombre, "Servicios básicos")
        self.assertEqual(movement.categoria, event.categoria)
        self.assertEqual(movement.fuente_clasificacion, "REGLA")
        resumen = resumen_gastos(
            self.user, date(2026, 8, 1), date(2026, 8, 1), "consumo"
        )
        grupo = next(g for g in resumen["categorias"] if g["categoria"] == "Servicios básicos")
        self.assertEqual(grupo["movimientos"][0]["descripcion"], "Aguas Andinas")
        self.assertEqual(resumen["totales"]["egresos_clp"], Decimal("25580"))

        self.client.force_login(self.user)
        pantalla = self.client.get(reverse("banca_movimientos"))
        self.assertContains(pantalla, "Desglosado por NeatPagos")
        self.assertContains(pantalla, "Aguas Andinas")
        self.assertContains(pantalla, "Servicios básicos")

    def test_neat_concilia_pago_dividido_con_un_solo_cargo(self):
        product = Producto.objects.create(user=self.user, banco=self.neat_bank, nombre="TDC Split", tipo="TDC")
        movement = MovimientoBancario.objects.create(
            user=self.user, producto=product, fecha=date(2026, 8, 2), descripcion="NEAT PAGOS 1234",
            monto=Decimal("300"), monto_clp=Decimal("300"), hash_externo="neat-split",
        )
        ingestar_pagos_neat(self.user, [
            PagoExternoNormalizado("N-A", "CONFIRMADO", Decimal("100"), tarjeta_ultimos4="1234", fecha_cargo=date(2026, 8, 1)),
            PagoExternoNormalizado("N-B", "CONFIRMADO", Decimal("200"), tarjeta_ultimos4="1234", fecha_cargo=date(2026, 8, 1)),
        ])
        conciliar_eventos(self.user)
        rows = ConciliacionEventoExterno.objects.filter(movimiento=movement)
        self.assertEqual(rows.count(), 2)
        self.assertEqual(sum((row.monto_asignado for row in rows), Decimal("0")), Decimal("300"))

        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Gastos de tercero")
        evento_recuperable = EventoFinancieroExterno.objects.get(external_id="N-A")
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("banca_asignar_cuenta_cobrar_neat", args=[evento_recuperable.pk]),
            {"cuenta_cobro": cuenta.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            DetalleCuentaPorCobrar.objects.filter(
                cuenta=cuenta, evento_externo=evento_recuperable, monto=Decimal("100")
            ).exists()
        )
        evento_recuperable.refresh_from_db()
        self.assertEqual(
            evento_recuperable.categoria.nombre, "Pagos por cuenta de terceros"
        )
        self.assertTrue(evento_recuperable.categoria_es_manual)

        consumo = resumen_gastos(
            self.user, date(2026, 8, 1), date(2026, 8, 1), "consumo"
        )
        self.assertEqual(consumo["totales"]["egresos_clp"], Decimal("200"))
        self.assertNotIn(
            "Cuenta por cobrar", [grupo["categoria"] for grupo in consumo["categorias"]]
        )

        pantalla = self.client.get(reverse("banca_movimientos"))
        self.assertContains(pantalla, "por cobrar")
        self.assertContains(pantalla, "Gastos de tercero")

        cuenta_ajena = CuentaPorCobrar.objects.create(
            user=self.other, nombre="Cuenta ajena"
        )
        denegado = self.client.post(
            reverse("banca_asignar_cuenta_cobrar_neat", args=[evento_recuperable.pk]),
            {"cuenta_cobro": cuenta_ajena.pk},
        )
        self.assertEqual(denegado.status_code, 404)
        self.assertEqual(
            DetalleCuentaPorCobrar.objects.get(evento_externo=evento_recuperable).cuenta,
            cuenta,
        )

    def test_neat_separa_hipoteca_de_arriendo(self):
        ingestar_pagos_neat(
            self.user,
            [
                PagoExternoNormalizado(
                    external_id="N-HIPOTECA",
                    estado="CONFIRMADO",
                    monto_pagado=Decimal("475901"),
                    cuenta="Scotiabank - Hipotecario",
                    fecha_cargo=date(2026, 8, 2),
                )
            ],
        )

        evento = EventoFinancieroExterno.objects.get(external_id="N-HIPOTECA")
        self.assertEqual(evento.categoria.nombre, "Financiamiento / Hipoteca")
        self.assertEqual(evento.fuente_clasificacion, "REGLA")

    def test_deposito_houm_agregado_se_asigna_por_propiedad(self):
        product = Producto.objects.create(user=self.user, banco=self.houm_bank, nombre="Cuenta", tipo="CUENTA")
        movement = MovimientoBancario.objects.create(
            user=self.user, producto=product, fecha=date(2026, 8, 6), descripcion="ABONO HOUM",
            monto=Decimal("-900"), monto_clp=Decimal("-900"), hash_externo="houm-aggregate",
        )
        second_department = Departamento.objects.create(
            user=self.user, codigo="E108", piso="10", metros_cuadrados=Decimal("45"),
            valor_compra_uf=Decimal("2900"), valor_actual_uf=Decimal("3100"), activo=True,
        )
        VinculoPropiedadExterna.objects.create(user=self.user, external_property_id="P-107", departamento=self.department)
        VinculoPropiedadExterna.objects.create(user=self.user, external_property_id="P-108", departamento=second_department)
        ingestar_liquidaciones_houm(self.user, [
            LiquidacionArriendoNormalizada("HA", "P-107", "E107", date(2026, 8, 1), Decimal("500"), Decimal("500"), fecha_deposito=date(2026, 8, 6)),
            LiquidacionArriendoNormalizada("HB", "P-108", "E108", date(2026, 8, 1), Decimal("400"), Decimal("400"), fecha_deposito=date(2026, 8, 6)),
        ])
        conciliar_eventos(self.user)
        rows = ConciliacionEventoExterno.objects.filter(movimiento=movement)
        self.assertEqual(rows.count(), 2)
        self.assertEqual(sum((row.monto_asignado for row in rows), Decimal("0")), Decimal("900"))

    def test_programacion_neat_y_houm_respeta_ventanas_y_detencion(self):
        zone = ZoneInfo("America/Santiago")
        self.assertTrue(debe_sincronizar(self.neat, datetime(2026, 9, 1, 10, tzinfo=zone)))
        self.assertFalse(debe_sincronizar(self.neat, datetime(2026, 9, 2, 10, tzinfo=zone)))
        self.assertTrue(debe_sincronizar(self.houm, datetime(2026, 9, 2, 10, tzinfo=zone)))
        self.assertFalse(debe_sincronizar(self.houm, datetime(2026, 9, 11, 10, tzinfo=zone)))
        link = VinculoPropiedadExterna.objects.create(user=self.user, external_property_id="P-107", departamento=self.department)
        event = EventoFinancieroExterno.objects.create(
            user=self.user, fuente="houm", tipo="LIQUIDACION", external_id="H-complete",
            periodo=date(2026, 9, 1), fecha_evento=date(2026, 9, 6), monto_neto=Decimal("500"),
        )
        LiquidacionHoum.objects.create(evento=event, vinculo_propiedad=link, external_property_id="P-107", propiedad_nombre="E107")
        self.assertTrue(houm_completo(self.houm, date(2026, 9, 1)))
        self.assertFalse(debe_sincronizar(self.houm, datetime(2026, 9, 7, 10, tzinfo=zone)))

    def test_lock_evitar_ejecuciones_simultaneas(self):
        self.neat.sincronizacion_en_curso = True
        self.neat.sincronizacion_iniciada = datetime.now(tz=ZoneInfo("UTC"))
        self.neat.save()
        self.assertIsNone(run_external_sync(self.neat, manual=True))

    def test_sync_manual_ignora_ventana_y_avanza_cursor(self):
        self.neat.conector_experimental = True
        self.neat.base_url = "https://solo-lectura.example"
        self.neat.save()
        connector = Mock()
        connector.get_payments.return_value = ResultadoExtraccionExterna(
            elementos=[PagoExternoNormalizado(
                "N-manual", "CONFIRMADO", Decimal("50"), fecha_cargo=date(2026, 8, 15)
            )], cursor="cursor-2"
        )
        with patch("bank_sync.registry.get_connector", return_value=connector):
            log = run_external_sync(self.neat, manual=True, period=date(2026, 8, 1))
        self.neat.refresh_from_db()
        self.assertTrue(log.exito)
        self.assertEqual(self.neat.ultimo_cursor, "cursor-2")
        connector.close.assert_called_once()

    def test_mfa_o_captcha_detiene_y_solicita_reautenticacion(self):
        self.houm.conector_experimental = True
        self.houm.base_url = "https://solo-lectura.example"
        self.houm.save()
        connector = Mock()
        connector.authenticate.side_effect = InteractiveAuthRequired("MFA requerido")
        with patch("bank_sync.registry.get_connector", return_value=connector):
            log = run_external_sync(self.houm, manual=True)
        self.houm.refresh_from_db()
        self.assertFalse(log.exito)
        self.assertTrue(self.houm.requiere_reautenticacion)
        self.assertEqual(self.houm.errores_consecutivos, 1)

    def test_panel_y_calendario_aislan_usuarios(self):
        ingestar_pagos_neat(self.other, [PagoExternoNormalizado(
            external_id="secret", estado="CONFIRMADO", monto_pagado=Decimal("99"),
            fecha_cargo=date(2026, 8, 1),
        )])
        self.client.login(username="externo", password="x")
        response = self.client.get("/banca/fuentes-externas/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "secret")
        link = VinculoPropiedadExterna.objects.create(user=self.user, external_property_id="P-107", departamento=self.department)
        events = eventos_externos_calendario(self.user, 2026, 8)
        self.assertEqual(events[0]["estado"], "estimado")
        self.assertEqual(events[0]["dia_cobro"], 6)
        self.assertNotIn("secret", str(events))

    def test_acciones_rechazan_credencial_de_otro_usuario(self):
        other_bank = Banco.objects.create(nombre="Neat Otro")
        other_credential = CredencialBanco.objects.create(
            user=self.other, banco=other_bank, conector="neat", tipo_fuente="pagos"
        )
        self.client.login(username="externo", password="x")
        response = self.client.post(f"/banca/fuentes-externas/{other_credential.pk}/sync/")
        self.assertEqual(response.status_code, 404)
