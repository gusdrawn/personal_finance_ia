"""Regresiones del ciclo de revisión, confirmación y conciliación mensual."""

from datetime import date
from decimal import Decimal
from importlib import import_module
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.apps import apps
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.db import connection
from django.test import TestCase
from django.urls import reverse

from banca.models import ConciliacionEventoExterno, EventoFinancieroExterno, MovimientoBancario, PagoNeat
from configuracion.models import Banco, Producto
from gastos.services import resumen_gastos
from patrimonio.models import AbonoCuentaPorCobrar, CalculoCuentaPorCobrar, CuentaPorCobrar, DetalleCuentaPorCobrar, PartidaRevisionCobro
from patrimonio.services.revision_cobros import (
    confirmar_calculo, guardar_decisiones, preparar_calculo, proponer_partida, registrar_referencia,
)
from patrimonio.services.cuentas_por_cobrar import texto_cobro

SEP = date(2026, 9, 1)
OCT = date(2026, 10, 1)


class RevisionCobrosTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("revision_cobros", password="secret")
        self.other = User.objects.create_user("revision_ajena", password="secret")
        self.banco = Banco.objects.create(nombre="Banco revisión")
        self.producto = Producto.objects.create(user=self.user, banco=self.banco, nombre="Cuenta revisión", tipo="CUENTA")
        self.cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Wilder")
        self.base = DetalleCuentaPorCobrar.objects.create(cuenta=self.cuenta, periodo=SEP, fecha=date(2026, 9, 4), descripcion="Saldo anterior", monto=1052244)
        self.client.force_login(self.user)

    def movimiento(self, monto="-100000", fecha=date(2026, 10, 3), user=None, descripcion="Transferencia recibida", moneda="CLP"):
        user = user or self.user
        producto = self.producto if user == self.user else Producto.objects.create(user=user, banco=self.banco, nombre="Ajena", tipo="CUENTA")
        return MovimientoBancario.objects.create(user=user, producto=producto, fecha=fecha, descripcion=descripcion, moneda=moneda,
            monto=Decimal(monto), monto_clp=Decimal(monto), hash_externo=f"revision-{MovimientoBancario.objects.count()}", estado="FACTURADO")

    def revisar(self, calculo, partidas, decision="APLICAR"):
        decisiones = {str(p.pk): {"decision": decision} for p in partidas}
        guardar_decisiones(calculo, decisiones)
        calculo.refresh_from_db()
        return calculo

    def confirmar(self, calculo, partidas, decision="APLICAR"):
        calculo = self.revisar(calculo, partidas, decision)
        return confirmar_calculo(calculo, calculo.huella)

    def test_edicion_historica_se_revisa_en_mes_de_la_base_y_aplica_solo_diferencia(self):
        agosto = date(2026, 8, 1)
        detalle = DetalleCuentaPorCobrar.objects.create(
            cuenta=self.cuenta, periodo=agosto, fecha=date(2026, 8, 28),
            descripcion="Monto Pasado 5 de Agosto", monto=822561,
        )
        referencia = registrar_referencia(self.cuenta, SEP, date(2026, 9, 4), usuario=False)
        texto, desglose, saldo = referencia.texto, referencia.desglose, referencia.saldo_final
        borrador = preparar_calculo(self.cuenta, SEP)
        for _ in range(2):
            proponer_partida(self.cuenta, "EDITAR", periodo=agosto, detalle=detalle,
                             fecha=detalle.fecha, descripcion=detalle.descripcion, monto=741864)
        partida = self.cuenta.partidas_revision.get(tipo="EDITAR", detalle=detalle)
        self.assertEqual(partida.periodo, SEP)
        self.assertEqual(partida.monto, Decimal("741864"))
        self.assertEqual(partida.decision, "PENDIENTE")
        detalle.refresh_from_db()
        self.assertEqual(detalle.monto, Decimal("822561"))
        self.assertEqual(self.cuenta.saldo_pendiente, saldo)
        with self.assertRaisesMessage(ValidationError, "Los datos cambiaron"):
            confirmar_calculo(borrador, borrador.huella)

        confirmado = self.confirmar(preparar_calculo(self.cuenta, SEP), [partida])
        self.assertEqual(confirmado.ajustes, Decimal("-80697"))
        self.assertEqual(confirmado.cargos, 0)
        self.assertEqual(confirmado.saldo_final, saldo - Decimal("80697"))
        detalle.refresh_from_db()
        self.assertEqual(detalle.monto, Decimal("741864"))
        self.assertEqual(detalle.periodo, agosto)
        self.assertEqual(detalle.fecha, date(2026, 8, 28))
        self.assertEqual(confirmado.desglose["entradas"][0]["periodo_original"], "2026-08-01")
        referencia.refresh_from_db()
        self.assertEqual((referencia.texto, referencia.desglose, referencia.saldo_final), (texto, desglose, saldo))

    def test_edicion_pendiente_existente_se_muestra_y_precarga_sin_modificar_datos(self):
        agosto = date(2026, 8, 1)
        detalle = DetalleCuentaPorCobrar.objects.create(
            cuenta=self.cuenta, periodo=agosto, fecha=date(2026, 8, 28),
            descripcion="Monto Pasado 5 de Agosto", monto=822561,
        )
        registrar_referencia(self.cuenta, SEP, date(2026, 9, 4), usuario=False)
        # Conserva una edición guardada antes de corregir el mes de aplicación.
        partida = PartidaRevisionCobro.objects.create(
            cuenta=self.cuenta, periodo=agosto, tipo="EDITAR", clave=f"editar:{detalle.pk}",
            detalle=detalle, fecha=date(2026, 8, 5), monto=741864,
            descripcion="Saldo corregido de agosto", provisional=True,
        )
        anteriores = list(self.cuenta.calculos.values())
        respuesta = self.client.get(
            reverse("cuentas_por_cobrar"),
            {"periodo": "2026-08", "cuenta": self.cuenta.pk},
        )
        self.assertContains(respuesta, "Cambio pendiente")
        self.assertContains(respuesta, "$741.864")
        self.assertContains(respuesta, "Registrado: $822.561")
        self.assertContains(respuesta, "Revisar en 09/2026")
        edicion = self.client.get(reverse("cuentas_por_cobrar"), {"periodo": "2026-08", "editar": detalle.pk})
        self.assertContains(edicion, 'value="741864.00"')
        self.assertContains(edicion, 'value="2026-08-05"')
        self.assertContains(edicion, 'value="Saldo corregido de agosto"')
        # Reconstruir conserva el mes original en el formulario; la revisión
        # patrimonial sigue apuntando a septiembre por la referencia existente.
        self.assertEqual(edicion.context["cuentas_por_cobrar"][0].periodo_edicion, agosto)
        self.assertTrue(edicion.context["cuentas_por_cobrar"][0].valores_edicion.provisional)
        self.assertEqual(list(self.cuenta.calculos.values()), anteriores)
        partida.refresh_from_db()
        self.assertEqual(partida.periodo, agosto)
        self.assertEqual(partida.monto, Decimal("741864"))
        detalle.refresh_from_db()
        self.assertEqual(detalle.monto, Decimal("822561"))
        self.client.force_login(self.other)
        ajena = self.client.get(reverse("cuentas_por_cobrar"), {"periodo": "2026-08", "editar": detalle.pk})
        self.assertNotContains(ajena, "$741.864")
        self.assertEqual(self.client.post(reverse("editar_detalle_cuenta_por_cobrar", args=[detalle.pk]),
                                         {"monto": "1"}).status_code, 404)
        partida.refresh_from_db()
        self.assertEqual(partida.monto, Decimal("741864"))

    def test_edicion_de_mes_cerrado_utiliza_mes_vigente_y_conserva_centavos(self):
        self.cuenta.moneda = "USD"
        self.cuenta.save()
        self.base.monto = Decimal("822.56")
        self.base.save()
        confirmado = self.confirmar(preparar_calculo(self.cuenta, OCT), [])
        partida = proponer_partida(self.cuenta, "EDITAR", periodo=SEP, detalle=self.base,
                                   monto=Decimal("741.86"), fecha=self.base.fecha,
                                   descripcion=self.base.descripcion)
        self.assertEqual(partida.periodo, OCT)
        siguiente = self.confirmar(preparar_calculo(self.cuenta, OCT), [partida])
        self.assertEqual(siguiente.ajustes, Decimal("-80.70"))
        self.assertEqual(siguiente.saldo_final, Decimal("741.86"))
        self.base.refresh_from_db()
        self.assertEqual(self.base.periodo, SEP)
        confirmado.refresh_from_db()
        self.assertEqual(confirmado.saldo_final, Decimal("822.56"))

    def test_edicion_heredada_no_se_aplica_antes_de_su_base_ni_duplica_linea(self):
        agosto = date(2026, 8, 1)
        detalle = DetalleCuentaPorCobrar.objects.create(
            cuenta=self.cuenta, periodo=agosto, fecha=date(2026, 8, 28),
            descripcion="Monto Pasado 5 de Agosto", monto=822561,
        )
        registrar_referencia(self.cuenta, SEP, date(2026, 9, 4), usuario=False)
        partida = PartidaRevisionCobro.objects.create(
            cuenta=self.cuenta, periodo=agosto, tipo="EDITAR", clave=f"editar:{detalle.pk}",
            detalle=detalle, fecha=detalle.fecha, monto=741864, descripcion=detalle.descripcion,
        )
        borrador = preparar_calculo(self.cuenta, agosto)
        self.assertFalse(self.cuenta.partidas_revision.filter(tipo="CARGO", detalle=detalle).exists())
        with self.assertRaisesMessage(ValidationError, "debe revisarse en 09/2026"):
            guardar_decisiones(borrador, {str(partida.pk): {"decision": "APLICAR"}})
        partida.refresh_from_db()
        self.assertEqual(partida.decision, "PENDIENTE")
        self.assertEqual(self.cuenta.saldo_pendiente, Decimal("1874805"))
        sin_saldo = preparar_calculo(self.cuenta, SEP, "sin_saldo")
        guardar_decisiones(sin_saldo, {str(partida.pk): {"decision": "APLICAR", "periodo": SEP}})
        sin_saldo.refresh_from_db()
        confirmado = confirmar_calculo(sin_saldo, sin_saldo.huella)
        self.assertEqual(confirmado.saldo_final, Decimal("741864"))
        self.assertEqual(self.cuenta.detalles.filter(pk=detalle.pk).count(), 1)

    def test_preparar_y_guardar_no_modifican_saldo_ni_crean_abono(self):
        mov = self.movimiento()
        self.client.post(reverse("banca_asignar_abono_cuenta_cobrar", args=[mov.pk]), {"cuenta_cobro": self.cuenta.pk})
        self.assertFalse(AbonoCuentaPorCobrar.objects.filter(movimiento=mov).exists())
        partida = self.cuenta.partidas_revision.get()
        calculo = preparar_calculo(self.cuenta, OCT)
        self.revisar(calculo, [partida])
        self.assertEqual(self.cuenta.saldo_pendiente, Decimal("1052244"))
        self.cuenta.activo_patrimonial.refresh_from_db()
        self.assertEqual(self.cuenta.activo_patrimonial.monto_original, Decimal("1052244"))
        mov.refresh_from_db()
        self.assertEqual(mov.naturaleza, "OPERACIONAL")
        confirmado = confirmar_calculo(calculo, calculo.huella)
        self.assertEqual(confirmado.saldo_final, Decimal("952244"))
        self.assertEqual(self.cuenta.saldo_pendiente, confirmado.saldo_final)
        self.assertTrue(AbonoCuentaPorCobrar.objects.filter(movimiento=mov, vigente=True).exists())

    def test_historial_y_texto_son_inmutables_y_correccion_ajusta_mes_abierto(self):
        septiembre = preparar_calculo(self.cuenta, SEP)
        ajuste = proponer_partida(self.cuenta, "AJUSTE", periodo=SEP, monto=100, descripcion="Corrección inicial", motivo="Revisión")
        septiembre = preparar_calculo(self.cuenta, SEP)
        septiembre = self.confirmar(septiembre, [ajuste])
        texto = septiembre.texto
        octubre = proponer_partida(self.cuenta, "EDITAR", periodo=OCT, detalle=self.base, monto=1052000, fecha=self.base.fecha, descripcion=self.base.descripcion)
        calculo = preparar_calculo(self.cuenta, OCT)
        confirmado = self.confirmar(calculo, [octubre])
        septiembre.refresh_from_db()
        self.assertEqual(septiembre.texto, texto)
        self.assertEqual(septiembre.saldo_final, Decimal("1052344"))
        self.assertEqual(confirmado.ajustes, Decimal("-244"))
        self.assertEqual(confirmado.saldo_final, Decimal("1052100"))
        self.assertEqual(texto_cobro(self.cuenta, SEP), texto)
        septiembre.texto = "alterado"
        with self.assertRaises(ValidationError):
            septiembre.save()
        with self.assertRaises(ValidationError):
            preparar_calculo(self.cuenta, SEP)

    def test_conciliar_manual_y_bancario_cuenta_una_vez_y_preserva_registros(self):
        self.base.monto = Decimal("1452244")
        self.base.save()
        manuales = [AbonoCuentaPorCobrar.objects.create(cuenta=self.cuenta, fecha=fecha, monto=monto) for fecha, monto in [(date(2026, 8, 6), 100000), (date(2026, 8, 20), 300000)]]
        referencia = registrar_referencia(self.cuenta, SEP, date(2026, 9, 4), monto_esperado=1052244)
        bancarios = []
        for manual in manuales:
            mov = self.movimiento(monto=str(-manual.monto), fecha=manual.fecha)
            bancario = AbonoCuentaPorCobrar.objects.create(cuenta=self.cuenta, movimiento=mov, fecha=manual.fecha, monto=manual.monto)
            bancarios.append(bancario)
        agosto = self.client.get(
            reverse("cuentas_por_cobrar"),
            {"periodo": "2026-08", "cuenta": self.cuenta.pk},
        )
        self.assertContains(agosto, "Abonos que podrían estar duplicados")
        self.assertContains(agosto, "Asociar desde Movimientos")
        self.assertContains(agosto, "$100.000")
        calculo = preparar_calculo(self.cuenta, OCT, referencia.pk)
        partidas = list(self.cuenta.partidas_revision.all())
        self.assertEqual(len(partidas), 2)
        self.assertEqual({p.conciliar_con_id for p in partidas}, {m.pk for m in manuales})
        agosto = self.client.get(
            reverse("cuentas_por_cobrar"),
            {"periodo": "2026-08", "cuenta": self.cuenta.pk},
        )
        self.assertContains(agosto, "Revisar conciliación")
        self.assertContains(agosto, f"#partida-{partidas[0].pk}")
        confirmado = self.confirmar(calculo, partidas, "CONCILIAR")
        self.assertEqual(confirmado.saldo_final, Decimal("1052244"))
        self.assertEqual(self.cuenta.abonos.filter(vigente=True).count(), 2)
        self.assertEqual(self.cuenta.abonos.count(), 4)
        self.assertEqual(self.cuenta.total_abonado, Decimal("400000"))
        caja = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "caja")
        self.assertEqual(caja["totales"]["ingresos_clp"], Decimal("400000"))
        consumo = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "consumo")
        self.assertEqual(consumo["totales"]["ingresos_clp"], Decimal("0"))
        for manual in manuales:
            manual.refresh_from_db()
            self.assertFalse(manual.vigente)
            self.assertIsNotNone(manual.conciliado_con_id)

    def test_transferencia_importada_tarde_como_respaldo_tiene_efecto_cero(self):
        registrar_referencia(self.cuenta, SEP, date(2026, 9, 4))
        mov = self.movimiento(fecha=date(2026, 8, 3), monto="-200000")
        partida = proponer_partida(self.cuenta, "ABONO", periodo=OCT, fuente=mov)
        calculo = preparar_calculo(self.cuenta, OCT)
        confirmado = self.confirmar(calculo, [partida], "RESPALDO")
        self.assertEqual(confirmado.saldo_final, Decimal("1052244"))
        self.assertEqual(confirmado.ajustes, Decimal("0"))
        self.assertEqual(confirmado.desglose["entradas"][0]["fecha"], "2026-08-03")
        nuevo = preparar_calculo(self.cuenta, OCT)
        self.assertEqual(nuevo.saldo_final, confirmado.saldo_final)
        self.assertEqual(nuevo.desglose["entradas"], [])

    def test_importado_tarde_aprobado_es_ajuste_y_no_usa_updated_at_como_corte(self):
        partida = proponer_partida(self.cuenta, "ABONO", periodo=OCT, fuente=self.movimiento(fecha=date(2026, 8, 3), monto="-200000"))
        confirmado = self.confirmar(preparar_calculo(self.cuenta, OCT), [partida])
        self.assertEqual(confirmado.ajustes, Decimal("-200000"))
        self.assertEqual(confirmado.abonos, Decimal("0"))
        self.assertEqual(confirmado.saldo_final, Decimal("852244"))

    def test_confirmacion_repetida_no_duplica_y_nueva_version_no_reaplica(self):
        partida = proponer_partida(self.cuenta, "ABONO", periodo=OCT, fuente=self.movimiento())
        confirmado = self.confirmar(preparar_calculo(self.cuenta, OCT), [partida])
        otra = confirmar_calculo(confirmado, confirmado.huella)
        self.assertEqual(otra.pk, confirmado.pk)
        self.assertEqual(self.cuenta.abonos.count(), 1)
        nueva = proponer_partida(self.cuenta, "CARGO", periodo=OCT, monto=2000, descripcion="Youtube", fecha=date(2026, 10, 4))
        segundo = self.confirmar(preparar_calculo(self.cuenta, OCT), [nueva])
        self.assertEqual(segundo.saldo_final, Decimal("954244"))
        self.assertEqual(segundo.anterior_id, confirmado.pk)
        self.assertEqual(self.cuenta.calculos.filter(estado="CONFIRMADO").count(), 2)

    def test_propuesta_desactualizada_rechaza_cambio_de_fuente_y_revisa_al_actualizar(self):
        mov = self.movimiento()
        partida = proponer_partida(self.cuenta, "ABONO", periodo=OCT, fuente=mov)
        calculo = self.revisar(preparar_calculo(self.cuenta, OCT), [partida])
        mov.monto = mov.monto_clp = Decimal("-150000")
        mov.save()
        with self.assertRaisesMessage(ValidationError, "Los datos cambiaron"):
            confirmar_calculo(calculo, calculo.huella)
        self.assertFalse(self.cuenta.abonos.exists())
        actualizado = preparar_calculo(self.cuenta, OCT)
        partida.refresh_from_db()
        self.assertEqual(partida.monto, Decimal("150000"))
        self.assertEqual(partida.decision, "PENDIENTE")
        self.assertEqual(actualizado.saldo_final, Decimal("1052244"))

    def test_propuesta_desactualizada_por_nueva_partida_o_decisiones(self):
        partida = proponer_partida(self.cuenta, "CARGO", periodo=OCT, monto=2000, descripcion="Nuevo cargo")
        calculo = self.revisar(preparar_calculo(self.cuenta, OCT), [partida])
        anterior = calculo.huella
        proponer_partida(self.cuenta, "CARGO", periodo=OCT, monto=3000, descripcion="Otro cargo")
        with self.assertRaises(ValidationError):
            confirmar_calculo(calculo, anterior)
        self.assertEqual(self.cuenta.saldo_pendiente, Decimal("1052244"))

    def test_exceso_abonos_rechaza_sin_materializar_ninguna_partida(self):
        partida = proponer_partida(self.cuenta, "ABONO", periodo=OCT, monto=2000000, descripcion="Abono")
        calculo = self.revisar(preparar_calculo(self.cuenta, OCT), [partida])
        self.assertLess(calculo.saldo_final, 0)
        with self.assertRaisesMessage(ValidationError, "exceden"):
            confirmar_calculo(calculo, calculo.huella)
        self.assertFalse(self.cuenta.abonos.exists())

    def test_pendiente_se_conserva_y_excluir_no_afecta_saldo(self):
        a = proponer_partida(self.cuenta, "CARGO", periodo=OCT, monto=2000, descripcion="Pendiente")
        b = proponer_partida(self.cuenta, "CARGO", periodo=OCT, monto=3000, descripcion="Ajeno")
        confirmado = self.confirmar(preparar_calculo(self.cuenta, OCT), [b], "EXCLUIR")
        self.assertEqual(confirmado.saldo_final, Decimal("1052244"))
        a.refresh_from_db(); b.refresh_from_db()
        self.assertIsNone(a.resuelta_en_id)
        self.assertEqual(b.resuelta_en_id, confirmado.pk)
        self.assertEqual(self.cuenta.detalles.count(), 1)

    def test_aislamiento_usuario_moneda_y_cuenta_archivada(self):
        ajena = self.movimiento(user=self.other)
        with self.assertRaises(ValidationError):
            proponer_partida(self.cuenta, "ABONO", periodo=OCT, fuente=ajena)
        usd = self.movimiento(moneda="USD")
        with self.assertRaises(ValidationError):
            proponer_partida(self.cuenta, "ABONO", periodo=OCT, fuente=usd)
        self.cuenta.activo = False
        self.cuenta.save()
        with self.assertRaises(ValidationError):
            preparar_calculo(self.cuenta, OCT)

    def test_endpoints_ajenos_no_permiten_revisar_ni_confirmar(self):
        partida = proponer_partida(self.cuenta, "CARGO", periodo=OCT, monto=100, descripcion="Cargo")
        calculo = preparar_calculo(self.cuenta, OCT)
        self.client.force_login(self.other)
        for nombre in ["guardar_revision_cobro", "confirmar_revision_cobro"]:
            self.assertEqual(self.client.post(reverse(nombre, args=[calculo.pk]), {"huella": calculo.huella, f"decision_{partida.pk}": "APLICAR"}).status_code, 404)

    def test_get_no_prepara_mes_formularios_colapsados_y_editar_abre_gestion(self):
        pagina = self.client.get(
            reverse("cuentas_por_cobrar"),
            {"periodo": "2026-10", "cuenta": self.cuenta.pk},
        )
        self.assertContains(pagina, "Pendiente de preparar")
        self.assertContains(pagina, f'id="gestion-cuenta-{self.cuenta.pk}" class="mt-5" >')
        self.assertFalse(self.cuenta.calculos.filter(estado="BORRADOR").exists())
        edicion = self.client.get(reverse("cuentas_por_cobrar") + f"?periodo=2026-10&editar={self.base.pk}")
        self.assertContains(edicion, f'id="gestion-cuenta-{self.cuenta.pk}" class="mt-5" open>')

    def test_migracion_conserva_valoracion_y_no_confirma_registros(self):
        schema = type("Schema", (), {"connection": connection})()
        migration = import_module("patrimonio.migrations.0008_abonocuentaporcobrar_conciliado_con_and_more")
        migration.conservar_saldos(apps, schema)
        self.assertEqual(self.cuenta.saldo_pendiente, Decimal("1052244"))
        self.assertEqual(self.cuenta.calculos.get().estado, "REFERENCIA")

    def test_retiro_de_abono_es_ajuste_confirmado_y_no_borra_historia(self):
        mov = self.movimiento()
        recibido = proponer_partida(self.cuenta, "ABONO", periodo=OCT, fuente=mov)
        primero = self.confirmar(preparar_calculo(self.cuenta, OCT), [recibido])
        abono = self.cuenta.abonos.get()
        retirar = proponer_partida(self.cuenta, "RETIRAR_ABONO", periodo=OCT, abono=abono)
        segundo = self.confirmar(preparar_calculo(self.cuenta, OCT), [retirar])
        self.assertEqual(segundo.saldo_final, Decimal("1052244"))
        self.assertEqual(primero.saldo_final, Decimal("952244"))
        abono.refresh_from_db()
        self.assertFalse(abono.vigente)

    def test_no_aplica_fuente_confirmada_en_otra_cuenta(self):
        mov = self.movimiento()
        otra = CuentaPorCobrar.objects.create(user=self.user, nombre="Otra cuenta")
        AbonoCuentaPorCobrar.objects.create(cuenta=otra, movimiento=mov, fecha=mov.fecha, monto=100000)
        with self.assertRaises(ValidationError):
            proponer_partida(self.cuenta, "ABONO", periodo=OCT, fuente=mov)

    def test_pagos_iguales_no_se_unifican_sin_decision_y_conciliacion_ambigua_no_se_adivina(self):
        for _ in range(2):
            AbonoCuentaPorCobrar.objects.create(cuenta=self.cuenta, fecha=date(2026, 8, 6), monto=100000)
        partida = proponer_partida(self.cuenta, "ABONO", periodo=OCT, fuente=self.movimiento(fecha=date(2026, 8, 6)))
        self.assertIsNone(partida.conciliar_con_id)
        self.assertEqual(partida.decision, "PENDIENTE")
        calculo = preparar_calculo(self.cuenta, OCT)
        with self.assertRaises(ValidationError):
            self.revisar(calculo, [partida], "CONCILIAR")

    def test_conciliar_duplicado_ya_descontado_corrige_solo_mes_abierto(self):
        fecha = date(2026, 8, 6)
        manual = AbonoCuentaPorCobrar.objects.create(cuenta=self.cuenta, fecha=fecha, monto=100000)
        mov = self.movimiento(fecha=fecha)
        bancario = AbonoCuentaPorCobrar.objects.create(cuenta=self.cuenta, fecha=fecha, monto=100000, movimiento=mov)
        referencia = registrar_referencia(self.cuenta, SEP, date(2026, 9, 4), usuario=False)
        self.assertEqual(referencia.saldo_final, Decimal("852244"))
        partida = proponer_partida(self.cuenta, "ABONO", periodo=OCT, fuente=mov)
        self.assertEqual(partida.conciliar_con_id, manual.pk)
        confirmado = self.confirmar(preparar_calculo(self.cuenta, OCT), [partida], "CONCILIAR")
        self.assertEqual(confirmado.ajustes, Decimal("100000"))
        self.assertEqual(confirmado.saldo_final, Decimal("952244"))
        referencia.refresh_from_db()
        self.assertEqual(referencia.saldo_final, Decimal("852244"))
        self.assertEqual(self.cuenta.abonos.filter(vigente=True).get().pk, bancario.pk)

    def test_confirmar_saldo_sin_efectos_nuevos_conserva_pendientes(self):
        pendiente = proponer_partida(self.cuenta, "ABONO", periodo=OCT, monto=300000, descripcion="Sin validar")
        calculo = preparar_calculo(self.cuenta, OCT)
        confirmado = confirmar_calculo(calculo, calculo.huella)
        self.assertEqual(confirmado.saldo_final, Decimal("1052244"))
        self.assertEqual(confirmado.desglose["entradas"], [])
        pendiente.refresh_from_db()
        self.assertIsNone(pendiente.resuelta_en_id)
        self.assertFalse(self.cuenta.abonos.exists())

    def test_dos_transferencias_no_pueden_conciliar_un_mismo_abono(self):
        fecha = date(2026, 8, 6)
        manual = AbonoCuentaPorCobrar.objects.create(cuenta=self.cuenta, fecha=fecha, monto=100000)
        registrar_referencia(self.cuenta, SEP, date(2026, 9, 4))
        partidas = [proponer_partida(self.cuenta, "ABONO", periodo=OCT, fuente=self.movimiento(fecha=fecha)) for _ in range(2)]
        calculo = self.revisar(preparar_calculo(self.cuenta, OCT), partidas, "CONCILIAR")
        with self.assertRaises(ValidationError):
            confirmar_calculo(calculo, calculo.huella)
        self.assertEqual(self.cuenta.abonos.count(), 1)
        manual.refresh_from_db()
        self.assertIsNone(manual.movimiento_id)
        self.assertFalse(self.cuenta.calculos.filter(estado="CONFIRMADO").exists())

    def test_fuentes_banco_y_neat_solapadas_rechazan_confirmacion_atomica(self):
        mov = self.movimiento(monto="100000", descripcion="Neat tarjeta")
        evento = EventoFinancieroExterno.objects.create(user=self.user, fuente="neat", tipo="PAGO", external_id="revision-solapada", moneda="CLP", fecha_cargo=mov.fecha, monto_neto=100000)
        PagoNeat.objects.create(evento=evento, total_cargado=100000)
        ConciliacionEventoExterno.objects.create(evento=evento, movimiento=mov, rol="CARGO", monto_asignado=100000)
        banco = proponer_partida(self.cuenta, "CARGO", periodo=OCT, fuente=mov)
        neat = proponer_partida(self.cuenta, "CARGO", periodo=OCT, fuente=evento)
        calculo = self.revisar(preparar_calculo(self.cuenta, OCT), [banco, neat])
        with self.assertRaises(ValidationError):
            confirmar_calculo(calculo, calculo.huella)
        self.assertEqual(self.cuenta.detalles.count(), 1)
        self.assertEqual(self.cuenta.saldo_pendiente, Decimal("1052244"))
        self.assertFalse(self.cuenta.calculos.filter(estado="CONFIRMADO").exists())

    def test_cambio_de_fuente_antes_de_guardar_decisiones_exige_actualizar(self):
        mov = self.movimiento()
        partida = proponer_partida(self.cuenta, "ABONO", periodo=OCT, fuente=mov)
        calculo = preparar_calculo(self.cuenta, OCT)
        mov.monto = mov.monto_clp = Decimal("-200000")
        mov.save()
        with self.assertRaisesMessage(ValidationError, "Actualiza la propuesta"):
            self.revisar(calculo, [partida])
        partida.refresh_from_db()
        self.assertEqual(partida.decision, "PENDIENTE")

    def test_reintentos_asociacion_e_importacion_no_reaplican_fuente(self):
        mov = self.movimiento()
        uno = proponer_partida(self.cuenta, "ABONO", periodo=OCT, fuente=mov)
        dos = proponer_partida(self.cuenta, "ABONO", periodo=OCT, fuente=mov)
        self.assertEqual(uno.pk, dos.pk)
        confirmado = self.confirmar(preparar_calculo(self.cuenta, OCT), [dos])
        mov.save()  # Una reimportación puede actualizar metadatos, no el cálculo.
        nuevo = preparar_calculo(self.cuenta, OCT)
        self.assertEqual(nuevo.saldo_final, confirmado.saldo_final)
        self.assertFalse(self.cuenta.partidas_revision.filter(resuelta_en__isnull=True).exists())
        reintento = proponer_partida(self.cuenta, "ABONO", periodo=OCT, fuente=mov)
        with self.assertRaisesMessage(ValidationError, "ya está incluida"):
            self.revisar(preparar_calculo(self.cuenta, OCT), [reintento])
        self.assertEqual(self.cuenta.abonos.count(), 1)

    def test_error_validacion_abre_panel_y_texto_historico_se_copia_sin_recalcular(self):
        texto = "Detalle conservado\nTotal pendiente: $1.052.244"
        referencia = registrar_referencia(self.cuenta, SEP, date(2026, 9, 4), texto=texto)
        self.client.post(reverse("registrar_abono_cuenta_por_cobrar"), {"cuenta_id": self.cuenta.pk, "monto": "0"}, follow=True)
        pagina = self.client.get(reverse("cuentas_por_cobrar"), {"periodo": "2026-10", "gestionar": self.cuenta.pk})
        self.assertContains(pagina, f'id="gestion-cuenta-{self.cuenta.pk}" class="mt-5" open>')
        self.assertContains(pagina, texto)
        self.assertContains(pagina, f'data-target="texto-historial-{referencia.pk}"')
        self.assertContains(pagina, "04/09/2026")
        crear = self.client.post(reverse("crear_cuenta_por_cobrar"), {"nombre": ""}, follow=True)
        self.assertContains(crear, 'id="crear-cuenta" class="glass rounded-2xl p-6 mt-8 border border-emerald-500/20" open>')

    def test_comando_referencia_simula_valida_usuario_y_es_idempotente(self):
        datos = {"periodo": "2026-09-01", "fecha": "2026-09-04", "saldo_final": "1052244", "detalle_ids": [self.base.pk], "abono_ids": [], "texto": "Texto aportado"}
        with TemporaryDirectory() as carpeta:
            archivo = Path(carpeta) / "referencia.json"
            archivo.write_text(json.dumps(datos), encoding="utf-8")
            opciones = {"user_id": self.user.pk, "cuenta_id": self.cuenta.pk, "archivo": str(archivo), "stdout": StringIO()}
            call_command("registrar_referencia_cobro", **opciones)
            self.assertFalse(self.cuenta.calculos.exists())
            for _ in range(2):
                call_command("registrar_referencia_cobro", apply=True, **opciones)
            self.assertEqual(self.cuenta.calculos.count(), 1)
            self.assertEqual(self.cuenta.calculos.get().estado, "REFERENCIA")
            self.assertEqual(self.cuenta.saldo_pendiente, Decimal("1052244"))
            with self.assertRaises(CommandError):
                call_command("registrar_referencia_cobro", apply=True, **{**opciones, "user_id": self.other.pk})

    def test_editar_fuente_ya_vinculada_actualiza_importe_y_solo_aplica_diferencia(self):
        mov = self.movimiento(monto="400000", fecha=date(2026, 9, 4))
        detalle = DetalleCuentaPorCobrar.objects.create(cuenta=self.cuenta, periodo=SEP, fecha=mov.fecha, descripcion="Arriendo", monto=400000, movimiento=mov, origen="MOVIMIENTO", monto_manual=False)
        referencia = registrar_referencia(self.cuenta, SEP, date(2026, 9, 4))
        mov.monto = mov.monto_clp = Decimal("405000")
        mov.save()
        edicion = proponer_partida(self.cuenta, "EDITAR", periodo=OCT, fuente=mov, detalle=detalle)
        confirmado = self.confirmar(preparar_calculo(self.cuenta, OCT), [edicion])
        self.assertEqual(confirmado.ajustes, Decimal("5000"))
        self.assertEqual(confirmado.saldo_final, referencia.saldo_final + 5000)
        detalle.refresh_from_db()
        self.assertEqual(detalle.monto, Decimal("405000"))
        self.assertEqual(referencia.desglose["contabilizados"][f"detalle:{detalle.pk}"], "400000.00")

    def test_respaldo_de_cargo_incluido_conserva_base_para_correcciones(self):
        registrar_referencia(self.cuenta, SEP, date(2026, 9, 4))
        respaldo = proponer_partida(self.cuenta, "CARGO", periodo=OCT, detalle=self.base, monto=self.base.monto, fecha=self.base.fecha, descripcion=self.base.descripcion)
        confirmado = self.confirmar(preparar_calculo(self.cuenta, OCT), [respaldo], "RESPALDO")
        self.assertEqual(confirmado.saldo_final, Decimal("1052244"))
        edicion = proponer_partida(self.cuenta, "EDITAR", periodo=OCT, detalle=self.base, monto=1052000, fecha=self.base.fecha, descripcion=self.base.descripcion)
        siguiente = self.confirmar(preparar_calculo(self.cuenta, OCT), [edicion])
        self.assertEqual(siguiente.ajustes, Decimal("-244"))
        self.assertEqual(siguiente.saldo_final, Decimal("1052000"))

    def test_respaldo_identifica_abono_incluido_sin_descontarlo_otra_vez(self):
        respaldo = proponer_partida(self.cuenta, "ABONO", periodo=OCT, fuente=self.movimiento())
        confirmado = self.confirmar(preparar_calculo(self.cuenta, OCT), [respaldo], "RESPALDO")
        self.assertEqual(confirmado.saldo_final, Decimal("1052244"))
        abono = self.cuenta.abonos.get()
        retiro = proponer_partida(self.cuenta, "RETIRAR_ABONO", periodo=OCT, abono=abono)
        siguiente = self.confirmar(preparar_calculo(self.cuenta, OCT), [retiro])
        self.assertEqual(siguiente.ajustes, Decimal("100000"))
        self.assertEqual(siguiente.saldo_final, Decimal("1152244"))

    def test_relaciones_de_conciliacion_y_confirmacion_rechazan_otra_cuenta(self):
        otra = CuentaPorCobrar.objects.create(user=self.other, nombre="Privada")
        abono_ajeno = AbonoCuentaPorCobrar.objects.create(cuenta=otra, fecha=date(2026, 10, 1), monto=100000)
        referencia_ajena = registrar_referencia(otra, OCT, date(2026, 10, 1))
        with self.assertRaises(ValidationError):
            AbonoCuentaPorCobrar.objects.create(cuenta=self.cuenta, fecha=abono_ajeno.fecha, monto=abono_ajeno.monto, conciliado_con=abono_ajeno)
        with self.assertRaises(ValidationError):
            PartidaRevisionCobro.objects.create(cuenta=self.cuenta, tipo="CARGO", clave="ajena", periodo=OCT, fecha=date(2026, 10, 1), monto=100, descripcion="Cargo", resuelta_en=referencia_ajena)

    def test_importes_no_finitos_se_rechazan_y_moneda_se_muestra_sin_ambiguedad(self):
        for monto in ["NaN", "Infinity", "-Infinity"]:
            response = self.client.post(reverse("registrar_abono_cuenta_por_cobrar"), {"cuenta_id": self.cuenta.pk, "monto": monto}, follow=True)
            self.assertContains(response, "El abono debe ser mayor que cero")
        self.assertFalse(self.cuenta.partidas_revision.exists())
        usd = CuentaPorCobrar.objects.create(user=self.user, nombre="Dólares", moneda="USD")
        DetalleCuentaPorCobrar.objects.create(cuenta=usd, periodo=SEP, fecha=date(2026, 9, 4), descripcion="Cuenta USD", monto=Decimal("1234.56"))
        pagina = self.client.get(reverse("cuentas_por_cobrar"), {"periodo": "2026-10"})
        self.assertContains(pagina, "US$1.234,56")
        self.assertContains(pagina, "$1.052.244")
