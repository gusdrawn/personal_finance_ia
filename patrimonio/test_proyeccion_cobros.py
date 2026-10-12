"""Meses independientes, reconstrucción provisional y arrastre del saldo."""

from datetime import date
from decimal import Decimal
from importlib import import_module

from django.apps import apps
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from banca.models import ConciliacionEventoExterno, EventoFinancieroExterno, MovimientoBancario, PagoNeat
from configuracion.models import Banco, Producto
from patrimonio.models import AbonoCuentaPorCobrar, CuentaPorCobrar, DetalleCuentaPorCobrar
from patrimonio.services.proyeccion_cobros import resumen_mensual
from patrimonio.services.revision_cobros import (
    confirmar_calculo, guardar_decisiones, preparar_calculo, proponer_partida, registrar_referencia,
)

JUL, AGO, SEP, OCT = [date(2026, mes, 1) for mes in (7, 8, 9, 10)]


class ProyeccionCobrosTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("mensual")
        self.otro = User.objects.create_user("otro_mensual")
        self.cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Wilder")
        self.client.force_login(self.user)

    def cargo(self, monto, periodo, descripcion="Cargo"):
        return DetalleCuentaPorCobrar.objects.create(
            cuenta=self.cuenta, monto=monto, periodo=periodo, fecha=periodo, descripcion=descripcion,
        )

    def test_totales_y_textos_son_del_mes_y_arrastran_solo_meses_anteriores(self):
        self.cargo(1000, JUL, "Julio")
        self.cargo(400, AGO, "Agosto")
        self.cargo(800, SEP, "Septiembre")
        AbonoCuentaPorCobrar.objects.create(cuenta=self.cuenta, monto=200, fecha=AGO)
        AbonoCuentaPorCobrar.objects.create(cuenta=self.cuenta, monto=100, fecha=SEP)
        proponer_partida(self.cuenta, "ABONO", monto=50, fecha=OCT, periodo=OCT, registrada_por_usuario=True)
        agosto, septiembre = resumen_mensual(self.cuenta, AGO), resumen_mensual(self.cuenta, SEP)
        self.assertEqual((agosto.saldo_anterior, agosto.cargos, agosto.abonos, agosto.saldo_final), (1000, 400, 200, 1200))
        self.assertEqual((septiembre.saldo_anterior, septiembre.cargos, septiembre.abonos, septiembre.saldo_final), (1200, 800, 100, 1900))
        self.assertIn("Abonos recibidos: -$200", agosto.texto)
        self.assertNotIn("Septiembre", agosto.texto)
        self.assertIn("Total pendiente: $1.200", agosto.texto)
        self.assertIn("Saldo pendiente anterior: $1.200", septiembre.texto)
        self.assertEqual(resumen_mensual(self.cuenta, OCT).saldo_final, 1850)
        self.assertFalse(self.cuenta.calculos.filter(estado__in=["BORRADOR", "CONFIRMADO"]).exists())

    def test_guardar_edicion_y_abono_en_agosto_recalcula_agosto_y_septiembre(self):
        detalle = self.cargo(822561, AGO, "Monto Pasado 5 de Agosto")
        self.cargo(82686, SEP, "Gastos comunes septiembre")
        referencia = registrar_referencia(self.cuenta, SEP, date(2026, 9, 4), usuario=False)
        saldo, texto = referencia.saldo_final, referencia.texto
        editar = self.client.post(reverse("editar_detalle_cuenta_por_cobrar", args=[detalle.pk]),
            {"periodo": "2026-08", "fecha": "2026-08-05", "descripcion": detalle.descripcion, "monto": "741864"})
        self.assertIn("periodo=2026-08", editar.url)
        self.client.post(reverse("registrar_abono_cuenta_por_cobrar"),
            {"cuenta_id": self.cuenta.pk, "periodo": "2026-08", "fecha": "2026-10-05", "monto": "100000"})
        agosto, septiembre = resumen_mensual(self.cuenta, AGO), resumen_mensual(self.cuenta, SEP)
        self.assertEqual((agosto.cargos, agosto.abonos, agosto.saldo_final), (741864, 100000, 641864))
        self.assertEqual(septiembre.saldo_anterior, agosto.saldo_final)
        self.assertEqual(septiembre.saldo_final, 724550)
        self.assertIn("Total pendiente: $641.864", agosto.texto)
        detalle.refresh_from_db()
        self.assertEqual(detalle.monto, 822561)
        self.assertEqual(self.cuenta.saldo_pendiente, saldo)
        referencia.refresh_from_db()
        self.assertEqual(referencia.texto, texto)

    def test_abono_manual_guardado_aparece_sin_confirmar_y_nunca_en_mes_anterior(self):
        self.client.post(reverse("agregar_detalle_cuenta_por_cobrar"),
            {"cuenta_id": self.cuenta.pk, "periodo": "2026-08", "fecha": "2026-08-05", "descripcion": "Arriendo", "monto": "1000"})
        self.client.post(reverse("registrar_abono_cuenta_por_cobrar"),
            {"cuenta_id": self.cuenta.pk, "periodo": "2026-08", "fecha": "2026-08-20", "monto": "300"})
        self.assertEqual(resumen_mensual(self.cuenta, JUL).saldo_final, 0)
        self.assertEqual(resumen_mensual(self.cuenta, AGO).saldo_final, 700)
        self.assertEqual(resumen_mensual(self.cuenta, SEP).saldo_anterior, 700)
        pagina = self.client.get(
            reverse("cuentas_por_cobrar"),
            {"periodo": "2026-08", "cuenta": self.cuenta.pk},
        )
        self.assertContains(pagina, "Total pendiente: $700")
        self.assertContains(pagina, "Total de 08/2026")
        cuenta_visible = pagina.context["cuentas_por_cobrar"][0]
        self.assertEqual(cuenta_visible.resumen_mensual.saldo_final, 700)
        self.assertFalse(self.cuenta.detalles.exists())
        self.assertFalse(self.cuenta.abonos.exists())

    def test_reconstruir_agosto_con_septiembre_confirmado_conserva_historia_y_arrastre(self):
        detalle = self.cargo(1000, AGO, "Saldo de agosto")
        self.cargo(300, SEP, "Septiembre")
        referencia = registrar_referencia(self.cuenta, SEP, SEP, saldo_anterior=500)
        previo = preparar_calculo(self.cuenta, SEP, str(referencia.pk))
        previo = confirmar_calculo(previo, previo.huella)
        texto, saldo = previo.texto, self.cuenta.saldo_pendiente
        self.assertEqual(saldo, 1800)

        editar = self.client.post(reverse("editar_detalle_cuenta_por_cobrar", args=[detalle.pk]),
            {"periodo": "2026-08", "fecha": "2026-08-05", "monto": "900"})
        self.assertIn("periodo=2026-08", editar.url)
        self.client.post(reverse("registrar_abono_cuenta_por_cobrar"),
            {"cuenta_id": self.cuenta.pk, "periodo": "2026-08", "fecha": "2026-09-05", "monto": "200"})
        self.client.post(reverse("agregar_detalle_cuenta_por_cobrar"),
            {"cuenta_id": self.cuenta.pk, "periodo": "2026-08", "fecha": "2026-08-05", "descripcion": "Cargo histórico", "monto": "50"})
        for partida in self.cuenta.partidas_revision.filter(registrada_por_usuario=True):
            self.assertEqual((partida.periodo_vista, partida.periodo), (AGO, SEP))
        agosto, septiembre = resumen_mensual(self.cuenta, AGO), resumen_mensual(self.cuenta, SEP)
        self.assertEqual(agosto.saldo_final, 750)
        self.assertEqual(septiembre.saldo_anterior, agosto.saldo_final)
        self.assertEqual(septiembre.saldo_final, 1050)
        self.assertEqual(resumen_mensual(self.cuenta, OCT).saldo_anterior, 1050)
        pagina = self.client.get(reverse("cuentas_por_cobrar"), {"periodo": "2026-08", "editar": detalle.pk})
        cuenta = pagina.context["cuentas_por_cobrar"][0]
        self.assertEqual(cuenta.periodo_edicion, AGO)
        self.assertIn("periodo=2026-09", cuenta.url_preparacion_mensual)
        self.assertContains(pagina, "Total pendiente: $750")
        self.assertEqual(self.cuenta.saldo_pendiente, saldo)

        nuevo = preparar_calculo(self.cuenta, SEP, incluir_registros_guardados=True)
        nuevo = confirmar_calculo(nuevo, nuevo.huella)
        self.assertEqual(self.cuenta.abonos.get().periodo, AGO)
        self.assertEqual(self.cuenta.detalles.get(descripcion="Cargo histórico").periodo, AGO)
        self.assertEqual(resumen_mensual(self.cuenta, AGO).saldo_final, 750)
        self.assertEqual(resumen_mensual(self.cuenta, SEP).saldo_anterior, 750)
        self.assertEqual(resumen_mensual(self.cuenta, SEP).saldo_final, 1050)
        self.assertEqual(self.cuenta.saldo_pendiente, 1550)
        previo.refresh_from_db()
        self.assertEqual(previo.texto, texto)

    def test_ajuste_confirmado_se_arrastra_una_vez_y_en_su_mes(self):
        self.cargo(1000, AGO)
        ajuste = proponer_partida(self.cuenta, "AJUSTE", monto=-100, periodo=SEP,
                                 motivo="Corrección", registrada_por_usuario=True)
        self.assertEqual(resumen_mensual(self.cuenta, AGO).saldo_final, 1000)
        self.assertEqual(resumen_mensual(self.cuenta, SEP).saldo_final, 900)
        calculo = preparar_calculo(self.cuenta, SEP, incluir_registros_guardados=True)
        confirmado = confirmar_calculo(calculo, calculo.huella)
        confirmar_calculo(confirmado, confirmado.huella)
        self.assertEqual(resumen_mensual(self.cuenta, SEP).ajustes, -100)
        self.assertEqual(resumen_mensual(self.cuenta, SEP).saldo_final, 900)
        self.assertEqual(resumen_mensual(self.cuenta, OCT).saldo_anterior, 900)
        self.assertEqual(self.cuenta.partidas_revision.filter(tipo="AJUSTE", resuelta_en__isnull=False).count(), 1)

    def test_respaldos_confirmados_no_se_convierten_en_nuevos_efectos_mensuales(self):
        self.cargo(1000, AGO)
        cargo = proponer_partida(self.cuenta, "CARGO", monto=100, periodo=SEP, fecha=AGO,
                                descripcion="Sólo respaldo", registrada_por_usuario=True)
        abono = proponer_partida(self.cuenta, "ABONO", monto=200, periodo=SEP, fecha=AGO,
                                registrada_por_usuario=True)
        calculo = preparar_calculo(self.cuenta, SEP)
        guardar_decisiones(calculo, {str(p.pk): {"decision": "RESPALDO"} for p in [cargo, abono]})
        calculo.refresh_from_db()
        self.assertEqual(resumen_mensual(self.cuenta, SEP).saldo_final, 1000)
        confirmar_calculo(calculo, calculo.huella)
        self.assertEqual(resumen_mensual(self.cuenta, AGO).saldo_final, 1000)
        self.assertEqual(resumen_mensual(self.cuenta, SEP).saldo_final, 1000)
        self.assertEqual(resumen_mensual(self.cuenta, OCT).saldo_anterior, 1000)

    def test_retirar_en_septiembre_conserva_agosto_antes_y_despues_de_confirmar(self):
        detalle = self.cargo(1000, AGO)
        abono = AbonoCuentaPorCobrar.objects.create(cuenta=self.cuenta, monto=200, fecha=AGO, periodo=AGO)
        retirar_cargo = proponer_partida(self.cuenta, "RETIRAR_CARGO", detalle=detalle,
                                        periodo=SEP, registrada_por_usuario=True)
        retirar_abono = proponer_partida(self.cuenta, "RETIRAR_ABONO", abono=abono,
                                        periodo=SEP, registrada_por_usuario=True)
        self.assertEqual(resumen_mensual(self.cuenta, AGO).saldo_final, 800)
        self.assertEqual(resumen_mensual(self.cuenta, SEP).saldo_final, 0)
        calculo = preparar_calculo(self.cuenta, SEP, incluir_registros_guardados=True)
        confirmar_calculo(calculo, calculo.huella)
        self.assertEqual(resumen_mensual(self.cuenta, AGO).saldo_final, 800)
        self.assertEqual(resumen_mensual(self.cuenta, SEP).saldo_anterior, 800)
        self.assertEqual(resumen_mensual(self.cuenta, SEP).saldo_final, 0)
        self.assertEqual(resumen_mensual(self.cuenta, OCT).saldo_anterior, 0)

    def test_cambios_de_un_mes_futuro_no_modifican_el_mes_anterior(self):
        detalle = self.cargo(1000, AGO)
        proponer_partida(self.cuenta, "EDITAR", detalle=detalle, monto=900, periodo=SEP,
                        fecha=AGO, registrada_por_usuario=True)
        self.assertEqual(resumen_mensual(self.cuenta, AGO).saldo_final, 1000)
        septiembre = resumen_mensual(self.cuenta, SEP)
        self.assertEqual((septiembre.saldo_anterior, septiembre.ajustes, septiembre.saldo_final), (1000, -100, 900))

    def test_sugerencias_automaticas_se_revisan_y_no_se_aplican_al_navegar(self):
        self.cargo(1000, AGO)
        sugerida = proponer_partida(self.cuenta, "CARGO", monto=200, periodo=SEP, fecha=SEP, descripcion="Sugerencia")
        self.assertEqual(resumen_mensual(self.cuenta, SEP).saldo_final, 1000)
        calculo = preparar_calculo(self.cuenta, SEP)
        guardar_decisiones(calculo, {str(sugerida.pk): {"decision": "APLICAR"}})
        self.assertEqual(resumen_mensual(self.cuenta, SEP).saldo_final, 1200)
        self.assertEqual(self.cuenta.saldo_pendiente, 1000)

    def test_confirmacion_conserva_historia_y_abono_guarda_periodo_distinto_de_fecha(self):
        detalle = self.cargo(1000, AGO)
        abono = proponer_partida(self.cuenta, "ABONO", monto=200, periodo=AGO, fecha=SEP, registrada_por_usuario=True)
        calculo = preparar_calculo(self.cuenta, AGO)
        guardar_decisiones(calculo, {str(abono.pk): {"decision": "APLICAR"}})
        calculo.refresh_from_db()
        confirmado = confirmar_calculo(calculo, calculo.huella)
        registro = self.cuenta.abonos.get()
        self.assertEqual(registro.periodo, AGO)
        self.assertEqual(registro.fecha, SEP)
        texto = confirmado.texto
        correccion = proponer_partida(self.cuenta, "EDITAR", monto=900, periodo=SEP, fecha=AGO,
                                     detalle=detalle, registrada_por_usuario=True)
        nuevo = preparar_calculo(self.cuenta, SEP)
        guardar_decisiones(nuevo, {str(correccion.pk): {"decision": "APLICAR"}})
        nuevo.refresh_from_db()
        confirmar_calculo(nuevo, nuevo.huella)
        self.assertEqual(resumen_mensual(self.cuenta, AGO).saldo_final, 800)
        self.assertEqual(resumen_mensual(self.cuenta, AGO).texto, texto)
        self.assertEqual(resumen_mensual(self.cuenta, OCT).saldo_anterior, 700)

    def test_consulta_sin_confirmar_mes_antiguo_recupera_importe_previo_a_correccion_futura(self):
        detalle = self.cargo(1000, AGO)
        registrar_referencia(self.cuenta, SEP, SEP, usuario=False)
        correccion = proponer_partida(self.cuenta, "EDITAR", monto=900, periodo=SEP, fecha=AGO,
                                     detalle=detalle, registrada_por_usuario=True)
        calculo = preparar_calculo(self.cuenta, SEP)
        guardar_decisiones(calculo, {str(correccion.pk): {"decision": "APLICAR"}})
        calculo.refresh_from_db()
        confirmar_calculo(calculo, calculo.huella)
        self.assertEqual(resumen_mensual(self.cuenta, AGO).saldo_final, 1000)
        self.assertEqual(resumen_mensual(self.cuenta, SEP).saldo_final, 900)

    def test_moneda_aislamiento_y_exceso_de_abonos(self):
        self.cuenta.moneda = "USD"
        self.cuenta.save()
        self.cargo(Decimal("10.25"), AGO)
        proponer_partida(self.cuenta, "ABONO", monto=Decimal("11.25"), periodo=AGO, fecha=AGO,
                        registrada_por_usuario=True)
        resumen = resumen_mensual(self.cuenta, AGO)
        self.assertEqual(resumen.saldo_final, -1)
        self.assertIn("Abonos en exceso: US$1,00", resumen.texto)
        pagina = self.client.get(
            reverse("cuentas_por_cobrar"),
            {"periodo": "2026-08", "cuenta": self.cuenta.pk},
        )
        cuenta_visible = pagina.context["cuentas_por_cobrar"][0]
        self.assertEqual(cuenta_visible.resumen_mensual.saldo_final, -1)
        self.client.force_login(self.otro)
        ajena = self.client.get(reverse("cuentas_por_cobrar"), {"periodo": "2026-08"})
        self.assertNotContains(ajena, "US$10,25")
        self.assertEqual(ajena.context["deuda_vigente_usd"], 0)

    def test_migracion_conserva_abonos_y_saldo_y_recupera_entrada_manual(self):
        self.cargo(1000, AGO)
        abono = AbonoCuentaPorCobrar.objects.create(cuenta=self.cuenta, monto=200, fecha=AGO)
        partida = proponer_partida(self.cuenta, "AJUSTE", monto=10, periodo=SEP, motivo="Manual")
        saldo = self.cuenta.saldo_pendiente
        migration = import_module("patrimonio.migrations.0009_cobros_periodos_y_vista_mensual")
        migration.conservar_periodos(apps, type("Schema", (), {"connection": connection})())
        abono.refresh_from_db()
        partida.refresh_from_db()
        self.assertEqual(abono.periodo, AGO)
        self.assertEqual(abono.monto, 200)
        self.assertTrue(partida.registrada_por_usuario)
        self.assertEqual(self.cuenta.saldo_pendiente, saldo)

    def test_registro_manual_pendiente_se_edita_sin_duplicarlo_y_aisla_usuario(self):
        partida = proponer_partida(self.cuenta, "CARGO", monto=1000, periodo=AGO, fecha=AGO,
                                  descripcion="Arriendo", registrada_por_usuario=True)
        url = reverse("editar_registro_pendiente_cobro", args=[partida.pk])
        form = self.client.get(reverse("cuentas_por_cobrar"), {"periodo": "2026-08", "editar_partida": partida.pk})
        self.assertContains(form, 'value="1000.00"')
        self.assertTrue(form.context["cuentas_por_cobrar"][0].gestionar)
        for _ in range(2):
            self.assertEqual(self.client.post(url, {"monto": "900", "periodo": "2026-08", "fecha": "2026-08-02"}).status_code, 302)
        self.assertEqual(self.cuenta.partidas_revision.count(), 1)
        self.assertEqual(resumen_mensual(self.cuenta, AGO).saldo_final, 900)
        self.assertEqual(resumen_mensual(self.cuenta, SEP).saldo_anterior, 900)
        invalido = self.client.post(url, {"monto": "-1"})
        self.assertIn(f"editar_partida={partida.pk}", invalido.url)
        partida.refresh_from_db()
        self.assertEqual(partida.monto, 900)
        self.client.force_login(self.otro)
        self.assertEqual(self.client.post(url, {"monto": "1"}).status_code, 404)
        partida.refresh_from_db()
        self.assertEqual(partida.monto, 900)

    def test_reconstruccion_manual_de_agosto_a_octubre_confirma_solo_expresamente(self):
        def guardar(tipo, mes, monto):
            return proponer_partida(self.cuenta, tipo, periodo=mes, fecha=mes, monto=monto,
                                    descripcion=tipo, registrada_por_usuario=True)

        def confirmar(mes, referencia=""):
            url = reverse("renovar_cuenta_por_cobrar", args=[self.cuenta.pk])
            self.client.post(url, {"periodo": f"{mes:%Y-%m}", "referencia_id": referencia,
                                   "incluir_registros_guardados": "1"})
            calculo = self.cuenta.calculos.get(estado="BORRADOR", periodo=mes)
            self.client.post(reverse("confirmar_revision_cobro", args=[calculo.pk]), {"huella": calculo.huella})
            calculo.refresh_from_db()
            self.assertEqual(calculo.estado, "CONFIRMADO")
            return calculo

        guardar("CARGO", AGO, 1000)
        guardar("ABONO", AGO, 200)
        self.assertEqual(resumen_mensual(self.cuenta, AGO).saldo_final, 800)
        self.assertEqual(self.cuenta.saldo_pendiente, 0)
        agosto = confirmar(AGO, "sin_saldo")
        texto_agosto = agosto.texto
        guardar("CARGO", SEP, 300)
        guardar("ABONO", SEP, 100)
        self.assertEqual(resumen_mensual(self.cuenta, SEP).saldo_anterior, 800)
        self.assertEqual(resumen_mensual(self.cuenta, SEP).saldo_final, 1000)
        self.assertEqual(self.cuenta.saldo_pendiente, 800)
        septiembre = confirmar(SEP)
        self.assertEqual(septiembre.saldo_anterior, 800)
        self.assertEqual(self.cuenta.saldo_pendiente, 1000)
        guardar("ABONO", OCT, 50)
        self.assertEqual(resumen_mensual(self.cuenta, OCT).saldo_final, 950)
        self.assertEqual(resumen_mensual(self.cuenta, AGO).texto, texto_agosto)
        self.assertEqual(self.cuenta.saldo_pendiente, 1000)
        calculos = self.cuenta.calculos.count()
        self.client.get(reverse("cuentas_por_cobrar"), {"periodo": "2026-10"})
        self.assertEqual(self.cuenta.calculos.count(), calculos)

    def test_excluir_edicion_no_retirara_el_registro_original(self):
        detalle = self.cargo(1000, AGO)
        cambio = proponer_partida(self.cuenta, "EDITAR", detalle=detalle, monto=900,
                                  periodo=AGO, fecha=AGO, registrada_por_usuario=True)
        calculo = preparar_calculo(self.cuenta, AGO, "sin_saldo")
        guardar_decisiones(calculo, {str(cambio.pk): {"decision": "EXCLUIR"}})
        calculo.refresh_from_db()
        confirmar_calculo(calculo, calculo.huella)
        detalle.refresh_from_db()
        self.assertTrue(detalle.vigente)
        self.assertEqual(detalle.monto, 1000)

    def test_fuentes_neat_y_banco_solapadas_se_muestran_una_vez_en_proyeccion(self):
        banco = Banco.objects.create(nombre="Banco mensual")
        producto = Producto.objects.create(user=self.user, banco=banco, nombre="Tarjeta", tipo="TDC")
        evento = EventoFinancieroExterno.objects.create(user=self.user, fuente="neat", tipo="PAGO",
            external_id="mensual-neat", periodo=AGO, fecha_cargo=AGO, moneda="CLP", monto_neto=1000)
        PagoNeat.objects.create(evento=evento, cuenta="Arriendo", total_cargado=1000)
        detalle = self.cargo(1000, AGO)
        detalle.evento_externo, detalle.origen = evento, "NEAT"
        detalle.save()
        movimiento = MovimientoBancario.objects.create(user=self.user, producto=producto, fecha=AGO,
            monto=1000, monto_clp=1000, moneda="CLP", descripcion="Neat", hash_externo="mensual-banco")
        ConciliacionEventoExterno.objects.create(evento=evento, movimiento=movimiento, rol="CARGO", monto_asignado=1000)
        proponer_partida(self.cuenta, "CARGO", fuente=movimiento, registrada_por_usuario=True)
        resumen = resumen_mensual(self.cuenta, AGO)
        self.assertEqual(resumen.saldo_final, 1000)
        self.assertEqual(len(resumen.entradas), 1)
        self.assertTrue(resumen.avisos)

    def test_pago_manual_y_bancario_iguales_se_advierten_sin_conciliar_automaticamente(self):
        self.cargo(1000, AGO)
        banco = Banco.objects.create(nombre="Banco abono mensual")
        producto = Producto.objects.create(user=self.user, banco=banco, nombre="Cuenta", tipo="CUENTA")
        movimiento = MovimientoBancario.objects.create(user=self.user, producto=producto, fecha=AGO,
            monto=-100, monto_clp=-100, moneda="CLP", descripcion="Abono", hash_externo="mensual-abono")
        AbonoCuentaPorCobrar.objects.create(cuenta=self.cuenta, monto=100, fecha=AGO)
        AbonoCuentaPorCobrar.objects.create(cuenta=self.cuenta, monto=100, fecha=AGO, movimiento=movimiento)
        resumen = resumen_mensual(self.cuenta, AGO)
        self.assertEqual(resumen.saldo_final, 800)
        self.assertTrue(any("Posible abono duplicado" in aviso for aviso in resumen.avisos))
        self.assertEqual(self.cuenta.abonos.filter(vigente=True).count(), 2)

    def test_varias_confirmaciones_del_mes_conservan_desglose_total_y_texto_mensual(self):
        cargo = proponer_partida(self.cuenta, "CARGO", monto=1000, periodo=AGO, fecha=AGO,
                                 descripcion="Arriendo", registrada_por_usuario=True)
        pago = proponer_partida(self.cuenta, "ABONO", monto=200, periodo=AGO, fecha=AGO,
                                registrada_por_usuario=True)
        primero = preparar_calculo(self.cuenta, AGO, "sin_saldo", incluir_registros_guardados=True)
        primero = confirmar_calculo(primero, primero.huella)
        texto = primero.texto
        proponer_partida(self.cuenta, "ABONO", monto=50, periodo=AGO, fecha=AGO, registrada_por_usuario=True)
        segundo = preparar_calculo(self.cuenta, AGO, incluir_registros_guardados=True)
        segundo = confirmar_calculo(segundo, segundo.huella)
        resumen = resumen_mensual(self.cuenta, AGO)
        self.assertEqual((resumen.saldo_anterior, resumen.cargos, resumen.abonos, resumen.saldo_final), (0, 1000, 250, 750))
        self.assertIn("- Arriendo: $1.000", resumen.texto)
        self.assertIn("Abonos recibidos: -$250", resumen.texto)
        self.assertIn("Total pendiente: $750", segundo.texto)
        primero.refresh_from_db()
        self.assertEqual(primero.texto, texto)
        self.assertEqual(resumen_mensual(self.cuenta, SEP).saldo_anterior, 750)

    def test_texto_confirmado_excluye_registros_manuales_que_dejaste_pendientes(self):
        cargo = proponer_partida(self.cuenta, "CARGO", monto=1000, periodo=AGO, fecha=AGO,
                                 descripcion="Aplicado", registrada_por_usuario=True)
        proponer_partida(self.cuenta, "CARGO", monto=500, periodo=AGO, fecha=AGO,
                        descripcion="Todavía pendiente", registrada_por_usuario=True)
        calculo = preparar_calculo(self.cuenta, AGO, "sin_saldo")
        guardar_decisiones(calculo, {str(cargo.pk): {"decision": "APLICAR"}})
        calculo.refresh_from_db()
        confirmado = confirmar_calculo(calculo, calculo.huella)
        self.assertEqual(confirmado.saldo_final, 1000)
        self.assertNotIn("Todavía pendiente", confirmado.texto)
        self.assertEqual(resumen_mensual(self.cuenta, AGO).saldo_final, 1500)
        self.assertTrue(resumen_mensual(self.cuenta, AGO).provisional)
