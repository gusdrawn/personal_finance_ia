"""Reconstrucción histórica, carga guiada y evidencia mensual de los cobros."""

from datetime import date
from decimal import Decimal
from io import StringIO
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command, CommandError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from banca.models import ConciliacionEventoExterno, EventoFinancieroExterno, ImportacionEstadoCuenta, MovimientoBancario, PagoNeat
from configuracion.models import Banco, Producto
from patrimonio.models import AbonoCuentaPorCobrar, CalculoCuentaPorCobrar, CuentaPorCobrar, DetalleCuentaPorCobrar, PartidaRevisionCobro
from patrimonio.services.cuentas_por_cobrar import clave_fuente, proponer_renovacion
from patrimonio.services.preparacion_cobros import evidencia_calculo, resumen_preparacion
from patrimonio.services.revision_cobros import confirmar_calculo, guardar_decisiones, preparar_calculo, proponer_partida, registrar_referencia

SEP, OCT = date(2026, 9, 1), date(2026, 10, 1)


class PreparacionCobrosTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("preparacion_cobro")
        self.other = User.objects.create_user("preparacion_ajena")
        banco = Banco.objects.create(nombre="Banco documentos")
        self.tarjeta = Producto.objects.create(user=self.user, banco=banco, nombre="Tarjeta 1234", tipo="TDC", ultimos4="1234")
        self.corriente = Producto.objects.create(user=self.user, banco=banco, nombre="Cuenta transferencias", tipo="CUENTA")
        self.cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Wilder")
        self.client.force_login(self.user)

    def neat(self, descripcion="Arriendo", monto=400000, fecha=date(2026, 8, 28), periodo=SEP):
        evento = EventoFinancieroExterno.objects.create(user=self.user, fuente="neat", tipo="PAGO", estado="CONFIRMADO",
            external_id=f"preparacion-{EventoFinancieroExterno.objects.count()}", moneda="CLP", fecha_cargo=fecha,
            fecha_confirmacion=fecha, periodo=periodo, monto_neto=monto)
        PagoNeat.objects.create(evento=evento, cuenta=descripcion, destinatario=descripcion,
                                tarjeta_ultimos4="1234", total_cargado=monto)
        return evento

    def detalle(self, descripcion="Arriendo", monto=400000, evento=None, periodo=SEP, fecha=date(2026, 8, 28)):
        return DetalleCuentaPorCobrar.objects.create(cuenta=self.cuenta, descripcion=descripcion, periodo=periodo,
            fecha=fecha, monto=monto, evento_externo=evento, origen="NEAT" if evento else "MANUAL",
            monto_manual=not bool(evento), clave_similitud=clave_fuente(evento) if evento else "")

    def movimiento(self, monto, fecha, descripcion="Transferencia Wilder", moneda="CLP", user=None):
        user = user or self.user
        producto = self.tarjeta if monto > 0 else self.corriente
        if user != self.user:
            producto = Producto.objects.create(user=user, banco=self.tarjeta.banco, nombre="Ajena", tipo=producto.tipo)
        return MovimientoBancario.objects.create(user=user, producto=producto, monto=monto, monto_clp=monto,
            fecha=fecha, descripcion=descripcion, moneda=moneda, estado="FACTURADO",
            periodo_factura=date(2026, 9, 25) if monto > 0 else None,
            hash_externo=f"doc-{MovimientoBancario.objects.count()}")

    def estado(self, cierre= date(2026, 9, 25), producto=None, user=None, moneda="CLP", **valores):
        return ImportacionEstadoCuenta.objects.create(user=user or self.user, producto=producto or self.tarjeta,
            nombre_archivo="cartola.pdf", hash_archivo=f"estado-{ImportacionEstadoCuenta.objects.count()}",
            fecha_estado=cierre, periodo_fin=cierre, periodo_inicio=date(2026, 8, 23), moneda=moneda,
            tipo_producto=(producto or self.tarjeta).tipo, **valores)

    def reconstruccion(self):
        for descripcion, monto, fecha in [
            ("Arriendo", 400000, date(2026, 8, 28)), ("Estacionamiento", 50000, date(2026, 8, 28)),
            ("Agua", 21910, date(2026, 9, 2)), ("Luz", 24380, date(2026, 9, 2)),
            ("Metrogas", 48707, date(2026, 9, 2)),
        ]:
            self.detalle(descripcion, monto, self.neat(descripcion, monto, fecha), fecha=fecha)
        self.detalle("Gastos Comunes", 82686, fecha=date(2026, 9, 2))
        self.detalle("Youtube Premium", 2000, fecha=date(2026, 9, 2))
        self.detalle("Monto Pasado 5 de Agosto", 822561, periodo=date(2026, 8, 1))
        manuales = [AbonoCuentaPorCobrar.objects.create(cuenta=self.cuenta, fecha=fecha, monto=monto)
                    for fecha, monto in [(date(2026, 8, 6), 100000), (date(2026, 8, 20), 300000)]]
        referencia = registrar_referencia(self.cuenta, SEP, date(2026, 9, 4), saldo_anterior=822561,
                                         cargos=629683, monto_esperado=1052244)
        return referencia, manuales

    def url(self):
        return reverse("renovar_cuenta_por_cobrar", args=[self.cuenta.pk])

    def test_reconstruye_septiembre_con_registros_y_sugiere_abonos_sin_descontar_dos_veces(self):
        referencia, manuales = self.reconstruccion()
        for manual in manuales:
            self.movimiento(-manual.monto, manual.fecha)
        calculo = preparar_calculo(self.cuenta, SEP, referencia.pk)
        partidas = list(self.cuenta.partidas_revision.all())
        self.assertEqual(calculo.saldo_final, Decimal("1052244"))
        self.assertEqual(len(partidas), 2)
        self.assertTrue(all(p.decision == "PENDIENTE" for p in partidas))
        self.assertEqual({p.conciliar_con_id for p in partidas}, {a.pk for a in manuales})
        self.assertEqual(len(evidencia_calculo(self.cuenta, referencia)), 10)
        pagina = self.client.get(reverse("cuentas_por_cobrar"), {"periodo": "2026-09", "gestionar": self.cuenta.pk})
        self.assertContains(pagina, "Desglose conservado y sus respaldos")
        self.assertContains(pagina, "Pago adelantado")
        self.assertContains(pagina, "$1.052.244")
        guardar_decisiones(calculo, {str(p.pk): {"decision": "CONCILIAR"} for p in partidas})
        calculo.refresh_from_db()
        confirmado = confirmar_calculo(calculo, calculo.huella)
        self.assertEqual(confirmado.saldo_final, referencia.saldo_final)
        self.assertEqual(self.cuenta.total_abonado, Decimal("400000"))
        self.assertEqual(referencia.estado, "REFERENCIA")

    def test_cartola_posterior_respalda_neat_anticipado_sin_crear_otra_obligacion(self):
        evento = self.neat()
        detalle = self.detalle(evento=evento)
        referencia = registrar_referencia(self.cuenta, SEP, date(2026, 9, 4))
        banco = self.movimiento(400000, date(2026, 8, 28), "NEAT")
        estado = self.estado()
        banco.importaciones.add(estado)
        ConciliacionEventoExterno.objects.create(evento=evento, movimiento=banco, rol="CARGO", monto_asignado=400000)
        calculo = preparar_calculo(self.cuenta, SEP)
        partida = self.cuenta.partidas_revision.get(movimiento=banco)
        with self.assertRaisesMessage(ValidationError, "ya está registrado"):
            guardar_decisiones(calculo, {str(partida.pk): {"decision": "APLICAR"}})
        guardar_decisiones(calculo, {str(partida.pk): {"decision": "RESPALDO"}})
        calculo.refresh_from_db()
        confirmado = confirmar_calculo(calculo, calculo.huella)
        self.assertEqual(confirmado.saldo_final, referencia.saldo_final)
        self.assertEqual(self.cuenta.detalles.count(), 1)
        detalle.refresh_from_db()
        self.assertEqual(detalle.evento_externo_id, evento.pk)
        self.assertEqual(confirmado.desglose["entradas"][0]["respalda_detalle_ids"], [detalle.pk])
        self.assertEqual(confirmado.desglose["contabilizados"][f"movimiento:{banco.pk}"], "0")
        fila = evidencia_calculo(self.cuenta, referencia)[0]
        self.assertTrue(fila["anticipado"])
        self.assertEqual(fila["movimientos"][0].estados_cobro[0].pk, estado.pk)

    def test_cargo_bancario_agrupado_respalda_varios_pagos_neat(self):
        eventos = [self.neat("Arriendo", 400000), self.neat("Estacionamiento", 50000)]
        detalles = [self.detalle(e.pago_neat.cuenta, e.monto_neto, e) for e in eventos]
        registrar_referencia(self.cuenta, SEP, date(2026, 9, 4))
        banco = self.movimiento(450000, date(2026, 8, 28), "NEAT AGRUPADO")
        for evento in eventos:
            ConciliacionEventoExterno.objects.create(evento=evento, movimiento=banco, rol="CARGO", monto_asignado=evento.monto_neto)
        calculo = preparar_calculo(self.cuenta, SEP)
        partida = self.cuenta.partidas_revision.get()
        guardar_decisiones(calculo, {str(partida.pk): {"decision": "RESPALDO"}})
        calculo.refresh_from_db()
        confirmado = confirmar_calculo(calculo, calculo.huella)
        self.assertEqual(confirmado.saldo_final, Decimal("450000"))
        self.assertEqual(self.cuenta.detalles.count(), 2)
        self.assertEqual(set(confirmado.desglose["entradas"][0]["respalda_detalle_ids"]), {d.pk for d in detalles})

    def test_fuente_neat_antigua_sugiere_correccion_con_importe_real_sin_reescribir_referencia(self):
        antigua = self.neat("Agua", 21910, date(2026, 6, 4), periodo=date(2026, 6, 1))
        detalle = self.detalle("Agua", 21910, antigua, fecha=date(2026, 9, 2))
        referencia = registrar_referencia(self.cuenta, SEP, date(2026, 9, 4))
        actual = self.neat("Agua", 19320, date(2026, 9, 2))
        calculo = preparar_calculo(self.cuenta, SEP)
        correccion = self.cuenta.partidas_revision.get(tipo="EDITAR")
        self.assertEqual(correccion.evento_externo_id, actual.pk)
        self.assertEqual(correccion.decision, "PENDIENTE")
        self.assertEqual(calculo.saldo_final, Decimal("21910"))
        fila = evidencia_calculo(self.cuenta, referencia)[0]
        self.assertTrue(fila["fuente_fuera_periodo"])
        self.assertEqual(fila["fecha_neat"], date(2026, 6, 4))
        patron = resumen_preparacion(self.cuenta, SEP)["patrones"][0]
        self.assertEqual(patron["nuevo_monto"], Decimal("19320"))
        self.assertIn("pendiente de revisar", patron["advertencia"])
        guardar_decisiones(calculo, {str(correccion.pk): {"decision": "APLICAR"}})
        calculo.refresh_from_db()
        self.assertEqual(calculo.ajustes, Decimal("-2590"))
        confirmado = confirmar_calculo(calculo, calculo.huella)
        self.assertEqual(confirmado.saldo_final, Decimal("19320"))
        detalle.refresh_from_db()
        self.assertEqual(detalle.evento_externo_id, actual.pk)
        referencia.refresh_from_db()
        self.assertEqual(referencia.saldo_final, Decimal("21910"))

    def test_correccion_neat_ambigua_no_elige_un_pago_por_importe_o_fecha_de_importacion(self):
        self.detalle("Agua", 21910, self.neat("Agua", 21910, date(2026, 6, 4)))
        registrar_referencia(self.cuenta, SEP, date(2026, 9, 4))
        self.neat("Agua", 19320, date(2026, 9, 2))
        self.neat("Agua", 21910, date(2026, 9, 3))
        preparar_calculo(self.cuenta, SEP)
        self.assertFalse(self.cuenta.partidas_revision.filter(tipo="EDITAR").exists())

    def test_cargar_cartola_antigua_hoy_no_cubre_mes_y_aisla_usuario_moneda_producto(self):
        self.detalle(evento=self.neat())
        self.estado(cierre=date(2026, 8, 25))
        self.estado(user=self.other)
        self.estado(moneda="USD")
        otra = Producto.objects.create(user=self.user, banco=self.tarjeta.banco, nombre="Otra tarjeta", tipo="TDC", ultimos4="9999")
        self.estado(producto=otra)
        tarjeta = resumen_preparacion(self.cuenta, SEP)["documentos"][0]
        self.assertFalse(tarjeta["completo"])
        self.assertEqual(tarjeta["disponibles"], [])
        self.assertEqual(tarjeta["ultimo"].fecha_estado, date(2026, 8, 25))
        correcta = self.estado()
        tarjeta = resumen_preparacion(self.cuenta, SEP)["documentos"][0]
        self.assertTrue(tarjeta["completo"])
        self.assertEqual([d.pk for d in tarjeta["disponibles"]], [correcta.pk])

    def test_preparar_ofrece_archivos_opcionales_y_no_copia_importes_historicos_sin_fuente(self):
        self.detalle(evento=self.neat())
        self.detalle("Gastos Comunes", 82686, fecha=date(2026, 9, 2))
        pagina = self.client.get(self.url(), {"periodo": "2026-10"})
        self.assertContains(pagina, 'name="estados"')
        self.assertContains(pagina, 'name="neat"')
        self.assertContains(pagina, "Falta importe actual")
        self.assertFalse(self.cuenta.calculos.exists())
        self.assertFalse(self.cuenta.partidas_revision.exists())
        aceptado = self.client.post(self.url(), {"periodo": "2026-10"})
        self.assertEqual(aceptado.status_code, 302)
        borrador = self.cuenta.calculos.get(estado="BORRADOR")
        self.assertEqual(borrador.cargos, 0)
        self.assertEqual(borrador.saldo_final, Decimal("482686"))
        self.assertFalse(self.cuenta.partidas_revision.exists())

    def test_referencia_opcional_no_obliga_a_reconstruir_septiembre_para_preparar_octubre(self):
        referencia, _ = self.reconstruccion()
        self.detalle("Registro adicional", 500, fecha=date(2026, 9, 12))
        legado = registrar_referencia(self.cuenta, SEP, date(2026, 9, 30), usuario=False)
        pagina = self.client.get(self.url(), {"periodo": "2026-10"})
        self.assertNotContains(pagina, "Primero reconstruye")
        self.assertEqual(pagina.context["anterior"], legado)
        self.assertEqual(self.client.post(self.url(), {"periodo": "2026-10"}).status_code, 302)
        borrador = self.cuenta.calculos.get(estado="BORRADOR", periodo=OCT)
        self.assertEqual(borrador.anterior, legado)
        elegido = preparar_calculo(self.cuenta, OCT, referencia.pk)
        self.assertEqual(elegido.anterior, referencia)
        cuenta_visible = self.client.get(reverse("cuentas_por_cobrar"), {"periodo": "2026-10"}).context["cuentas_por_cobrar"][0]
        self.assertEqual(cuenta_visible.calculo_para_copiar, legado)
        self.assertFalse(self.cuenta.calculos.filter(estado="CONFIRMADO").exists())
        referencia.refresh_from_db()
        self.assertEqual(referencia.saldo_final, Decimal("1052244"))

    def test_asignar_en_banca_descubre_cargo_sin_historial_ni_archivos_y_no_lo_repite(self):
        movimiento = self.movimiento(12500, date(2026, 9, 8), "Servicio para Wilder")
        respuesta = self.client.post(reverse("banca_asignar_cuenta_cobrar_movimiento", args=[movimiento.pk]), {"cuenta_cobro": self.cuenta.pk})
        self.assertIn("periodo=2026-09", respuesta.url)
        pagina = self.client.get(self.url(), {"periodo": "2026-09"})
        self.assertContains(pagina, "Servicio para Wilder")
        self.assertEqual([f["movimiento"].pk for f in pagina.context["preparacion"]["asignaciones"]], [movimiento.pk])
        self.assertEqual(self.client.post(self.url(), {"periodo": "2026-09"}).status_code, 302)
        borrador = self.cuenta.calculos.get(estado="BORRADOR")
        partida = self.cuenta.partidas_revision.get()
        self.assertEqual(partida.periodo, SEP)
        self.assertEqual(borrador.saldo_final, 0)
        self.assertFalse(self.cuenta.detalles.exists())
        guardar_decisiones(borrador, {str(partida.pk): {"decision": "APLICAR"}})
        borrador.refresh_from_db()
        confirmado = confirmar_calculo(borrador, borrador.huella)
        self.assertEqual(confirmado.saldo_final, Decimal("12500"))
        siguiente = preparar_calculo(self.cuenta, OCT)
        self.assertEqual(siguiente.saldo_final, confirmado.saldo_final)
        self.assertFalse(self.cuenta.partidas_revision.filter(resuelta_en__isnull=True).exists())
        resumen = resumen_preparacion(self.cuenta, OCT, confirmado)
        self.assertFalse(resumen["asignaciones"])
        self.assertEqual(len(resumen["contabilizados"]), 1)

    def test_transferencia_clasificada_despues_usa_fecha_original_y_se_puede_ignorar(self):
        self.detalle("Saldo registrado", 500000)
        transferencia = self.movimiento(-200000, date(2026, 9, 7))
        self.client.post(reverse("banca_asignar_abono_cuenta_cobrar", args=[transferencia.pk]), {"cuenta_cobro": self.cuenta.pk})
        partida = self.cuenta.partidas_revision.get()
        self.assertEqual(partida.periodo, SEP)
        borrador = preparar_calculo(self.cuenta, OCT)
        fila = resumen_preparacion(self.cuenta, OCT, borrador.anterior)["asignaciones"][0]
        self.assertTrue(fila["anterior"])
        guardar_decisiones(borrador, {str(partida.pk): {"decision": "EXCLUIR", "motivo": "Ya pagó un cobro anterior"}})
        borrador.refresh_from_db()
        confirmado = confirmar_calculo(borrador, borrador.huella)
        self.assertEqual(confirmado.saldo_final, Decimal("500000"))
        self.assertFalse(self.cuenta.abonos.exists())
        preparar_calculo(self.cuenta, OCT)
        self.assertFalse(self.cuenta.partidas_revision.filter(resuelta_en__isnull=True).exists())
        resumen = resumen_preparacion(self.cuenta, OCT, confirmado)
        self.assertEqual([p.pk for p in resumen["excluidos"]], [partida.pk])

    def test_fuente_ignorada_no_vuelve_como_sugerencia_recurrente(self):
        self.detalle(evento=self.neat())
        septiembre = preparar_calculo(self.cuenta, SEP)
        confirmar_calculo(septiembre, septiembre.huella)
        evento = self.neat(fecha=date(2026, 9, 28), periodo=OCT)
        partida = proponer_partida(self.cuenta, "CARGO", fuente=evento)
        octubre = preparar_calculo(self.cuenta, OCT)
        self.assertEqual(self.cuenta.partidas_revision.filter(resuelta_en__isnull=True).count(), 1)
        guardar_decisiones(octubre, {str(partida.pk): {"decision": "EXCLUIR"}})
        octubre.refresh_from_db()
        confirmar_calculo(octubre, octubre.huella)
        preparar_calculo(self.cuenta, OCT)
        self.assertEqual(proponer_renovacion(self.cuenta, OCT)["propuestas"], [])
        self.assertFalse(self.cuenta.partidas_revision.filter(resuelta_en__isnull=True).exists())

    def test_asociacion_de_otro_mes_se_traslada_solo_con_seleccion_explicita(self):
        transferencia = self.movimiento(-200000, date(2026, 9, 7))
        partida = proponer_partida(self.cuenta, "ABONO", fuente=transferencia, periodo=OCT)
        pagina = self.client.get(self.url(), {"periodo": "2026-09"})
        self.assertContains(pagina, 'name="trasladar_ids"')
        partida.refresh_from_db()
        self.assertEqual(partida.periodo, OCT)
        preparar_calculo(self.cuenta, SEP)
        partida.refresh_from_db()
        self.assertEqual(partida.periodo, OCT)
        self.assertEqual(self.client.post(self.url(), {"periodo": "2026-09", "trasladar_ids": [partida.pk]}).status_code, 302)
        partida.refresh_from_db()
        self.assertEqual(partida.periodo, SEP)
        self.assertEqual(partida.decision, "PENDIENTE")
        self.assertEqual(self.cuenta.calculos.get(estado="BORRADOR", periodo=SEP).saldo_final, 0)

    def test_traslado_rechaza_partidas_ajenas_y_movimientos_posteriores(self):
        ajena = CuentaPorCobrar.objects.create(user=self.other, nombre="Ajena")
        partida = proponer_partida(ajena, "CARGO", periodo=OCT, monto=1000, fecha=SEP)
        with self.assertRaisesMessage(ValidationError, "no pertenecen"):
            preparar_calculo(self.cuenta, SEP, trasladar_ids=[partida.pk])
        futura = proponer_partida(self.cuenta, "CARGO", periodo=OCT, monto=1000, fecha=date(2026, 10, 2))
        with self.assertRaisesMessage(ValidationError, "posterior"):
            preparar_calculo(self.cuenta, SEP, trasladar_ids=[futura.pk])
        futura.refresh_from_db()
        self.assertEqual(futura.periodo, OCT)

    def test_sin_saldo_inicial_es_opcion_explicita_y_no_reinicia_un_confirmado(self):
        referencia, _ = self.reconstruccion()
        sin_base = preparar_calculo(self.cuenta, OCT, "sin_saldo")
        self.assertIsNone(sin_base.anterior)
        self.assertEqual(sin_base.saldo_final, 0)
        self.assertTrue(self.cuenta.partidas_revision.exists())
        referencia.refresh_from_db()
        self.assertEqual(referencia.saldo_final, Decimal("1052244"))
        con_base = preparar_calculo(self.cuenta, OCT, referencia.pk)
        confirmado = confirmar_calculo(con_base, con_base.huella)
        with self.assertRaisesMessage(ValidationError, "saldo confirmado"):
            preparar_calculo(self.cuenta, OCT, "sin_saldo")
        self.assertEqual(self.cuenta.saldo_pendiente, confirmado.saldo_final)

    def test_cambiar_base_exige_revisar_decisiones_y_conserva_saldo_publicado(self):
        self.detalle("Saldo registrado", 500000)
        partida = proponer_partida(self.cuenta, "CARGO", periodo=OCT, monto=2000, fecha=OCT, descripcion="Youtube")
        borrador = preparar_calculo(self.cuenta, OCT)
        guardar_decisiones(borrador, {str(partida.pk): {"decision": "APLICAR"}})
        borrador = preparar_calculo(self.cuenta, OCT, "sin_saldo")
        partida.refresh_from_db()
        self.assertEqual(partida.decision, "PENDIENTE")
        self.assertEqual(borrador.saldo_final, 0)
        self.assertEqual(self.cuenta.saldo_pendiente, Decimal("500000"))

    def test_cartola_0776_importada_cubre_fuente_asignada_sin_exigir_tarjeta_historica(self):
        self.detalle(evento=self.neat(), periodo=date(2026, 8, 1))
        tarjeta = Producto.objects.create(user=self.user, banco=self.tarjeta.banco, nombre="TDC Santander ••••0776", tipo="TDC", ultimos4="0776")
        movimiento = self.movimiento(2000, date(2026, 9, 2), "Youtube Wilder")
        movimiento.producto = tarjeta
        movimiento.save()
        proponer_partida(self.cuenta, "CARGO", fuente=movimiento)
        estado = self.estado(cierre=date(2026, 9, 24), producto=tarjeta)
        documento = resumen_preparacion(self.cuenta, SEP)["documentos"][0]
        self.assertTrue(documento["completo"])
        self.assertEqual([d.pk for d in documento["disponibles"]], [estado.pk])
        self.assertEqual(documento["faltan_productos"], [])
        pagina = self.client.get(self.url(), {"periodo": "2026-09"})
        self.assertContains(pagina, "Cierre 24/09/2026")
        self.assertNotContains(pagina, "Falta completar")

    def test_fuentes_asignadas_aislan_usuario_moneda_y_reservan_una_sola_cuenta(self):
        movimiento = self.movimiento(10000, date(2026, 9, 2))
        proponer_partida(self.cuenta, "CARGO", fuente=movimiento)
        segunda = CuentaPorCobrar.objects.create(user=self.user, nombre="Otra persona")
        with self.assertRaisesMessage(ValidationError, "propuesta para otra"):
            proponer_partida(segunda, "CARGO", fuente=movimiento)
        ajena = self.movimiento(10000, date(2026, 9, 2), user=self.other)
        usd = self.movimiento(10, date(2026, 9, 2), moneda="USD")
        for fuente in [ajena, usd]:
            with self.assertRaisesMessage(ValidationError, "mismo usuario y moneda"):
                proponer_partida(self.cuenta, "CARGO", fuente=fuente)
        resumen = resumen_preparacion(self.cuenta, SEP)
        self.assertEqual([f["movimiento"].pk for f in resumen["asignaciones"]], [movimiento.pk])

    def test_banca_conserva_asignacion_pendiente_visible_y_permite_ignorarla(self):
        movimiento = self.movimiento(10000, date(2026, 9, 2), "Servicio Wilder")
        self.client.post(reverse("banca_asignar_cuenta_cobrar_movimiento", args=[movimiento.pk]), {"cuenta_cobro": self.cuenta.pk})
        pagina = self.client.get(reverse("banca_movimientos"))
        self.assertContains(pagina, "Asociado a Wilder · pendiente de validar")
        self.assertContains(pagina, "revisar cálculo")
        visible = next(m for m in pagina.context["movimientos"] if m.pk == movimiento.pk)
        self.assertEqual(visible.cuenta_cobro_id, self.cuenta.pk)
        self.client.post(reverse("banca_asignar_cuenta_cobrar_movimiento", args=[movimiento.pk]), {"cuenta_cobro": ""})
        partida = self.cuenta.partidas_revision.get()
        self.assertEqual(partida.decision, "EXCLUIR")
        calculo = preparar_calculo(self.cuenta, SEP)
        confirmado = confirmar_calculo(calculo, calculo.huella)
        self.assertEqual(confirmado.saldo_final, 0)
        self.assertFalse(self.cuenta.detalles.exists())

    def test_abono_ya_clasificado_puede_asignarse_desde_banca_y_respeta_mes_abierto(self):
        self.detalle("Saldo registrado", 500000)
        octubre = preparar_calculo(self.cuenta, OCT)
        confirmar_calculo(octubre, octubre.huella)
        movimiento = self.movimiento(-200000, date(2026, 9, 7))
        movimiento.fuente_clasificacion = "MANUAL"
        movimiento.save()
        pagina = self.client.get(reverse("banca_movimientos"))
        self.assertContains(pagina, reverse("banca_asignar_abono_cuenta_cobrar", args=[movimiento.pk]))
        self.client.post(reverse("banca_asignar_abono_cuenta_cobrar", args=[movimiento.pk]), {"cuenta_cobro": self.cuenta.pk})
        partida = self.cuenta.partidas_revision.get()
        self.assertEqual(partida.periodo, OCT)
        self.assertEqual(partida.fecha, date(2026, 9, 7))
        self.assertEqual(self.cuenta.saldo_pendiente, Decimal("500000"))

    @patch("banca.services.fuentes_externas.importar_archivo_externo")
    @patch("banca.services.estados_cuenta.importar_estado_cuenta")
    def test_subir_documentos_reutiliza_importadores_y_no_prepara_ni_confirma(self, banco, neat):
        self.detalle(evento=self.neat())
        banco.return_value = SimpleNamespace(estado="PROCESADO", mensaje="OK")
        neat.return_value = (SimpleNamespace(estado="PROCESADO", mensaje="OK"), False)
        pagina = self.client.post(self.url(), {
            "periodo": "2026-09", "accion": "importar",
            "estados": SimpleUploadedFile("septiembre.pdf", b"pdf sintetico"),
            "neat": SimpleUploadedFile("neat.csv", b"csv sintetico"),
        })
        self.assertEqual(pagina.status_code, 200)
        self.assertEqual(banco.call_args.args[0], self.user)
        self.assertEqual(neat.call_args.args[:2], (self.user, "neat"))
        self.assertFalse(self.cuenta.calculos.exists())
        self.assertEqual(self.cuenta.saldo_pendiente, Decimal("400000"))

    @patch("banca.services.estados_cuenta.importar_estado_cuenta")
    def test_archivo_invalido_y_importacion_fallida_no_preparan_saldos(self, importar):
        importar.return_value = SimpleNamespace(estado="ERROR", mensaje="No se pudo leer")
        pagina = self.client.post(self.url(), {"periodo": "2026-09", "accion": "importar",
            "estados": [SimpleUploadedFile("no.pdf", b"pdf incorrecto"), SimpleUploadedFile("no.csv", b"no") ]})
        self.assertContains(pagina, "No se pudo leer")
        self.assertEqual(importar.call_count, 1)
        self.assertFalse(self.cuenta.calculos.exists())

    def test_despues_de_preparar_cartola_nueva_obliga_a_actualizar_propuesta(self):
        self.detalle(evento=self.neat())
        calculo = preparar_calculo(self.cuenta, SEP)
        self.estado()
        with self.assertRaisesMessage(ValidationError, "Los datos cambiaron"):
            confirmar_calculo(calculo, calculo.huella)
        actualizado = preparar_calculo(self.cuenta, SEP)
        self.assertNotEqual(actualizado.huella, calculo.huella)

    def test_neat_ya_asignado_al_final_del_mes_no_se_reutiliza_para_el_siguiente(self):
        self.detalle(evento=self.neat(fecha=date(2026, 9, 2)))
        evento = self.neat(fecha=date(2026, 9, 28))
        self.detalle(evento=evento, fecha=date(2026, 9, 28))
        self.assertEqual(proponer_renovacion(self.cuenta, OCT)["propuestas"], [])

    def test_documentos_y_coincidencias_no_cruzan_cuentas_ni_usuarios(self):
        referencia, manuales = self.reconstruccion()
        self.movimiento(-100000, manuales[0].fecha, user=self.other)
        usd = self.movimiento(-300000, manuales[1].fecha, moneda="USD")
        calculo = preparar_calculo(self.cuenta, SEP)
        self.assertFalse(self.cuenta.partidas_revision.exists())
        self.assertFalse(any(f["coincidencias"] for f in evidencia_calculo(self.cuenta, referencia)))
        self.assertNotIn(usd.pk, [p.movimiento_id for p in self.cuenta.partidas_revision.all()])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertEqual(self.client.post(self.url(), {"accion": "importar"}).status_code, 404)
        self.assertEqual(calculo.saldo_final, Decimal("1052244"))

    def test_comando_reconstruccion_simula_y_guarda_un_solo_borrador_sin_confirmar(self):
        referencia, manuales = self.reconstruccion()
        self.movimiento(-100000, manuales[0].fecha)
        salida = StringIO()
        opciones = dict(user_id=self.user.pk, cuenta_id=self.cuenta.pk, periodo="2026-09", stdout=salida)
        call_command("reconstruir_cobro", **opciones)
        self.assertEqual(json.loads(salida.getvalue())["saldo_propuesto"], "1052244.00")
        self.assertEqual(self.cuenta.calculos.count(), 1)
        self.assertFalse(self.cuenta.partidas_revision.exists())
        for _ in range(2):
            call_command("reconstruir_cobro", apply=True, **{**opciones, "stdout": StringIO()})
        self.assertEqual(self.cuenta.calculos.filter(estado="BORRADOR").count(), 1)
        self.assertFalse(self.cuenta.calculos.filter(estado="CONFIRMADO").exists())
        self.assertEqual(self.cuenta.partidas_revision.count(), 1)
        referencia.refresh_from_db()
        self.assertEqual(referencia.saldo_final, Decimal("1052244"))
        self.assertEqual(self.cuenta.saldo_pendiente, Decimal("1052244"))
        with self.assertRaises(CommandError):
            call_command("reconstruir_cobro", **{**opciones, "user_id": self.other.pk})
