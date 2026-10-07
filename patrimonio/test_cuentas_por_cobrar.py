"""Regresiones de cuentas por cobrar, renovación e integración patrimonial."""

from datetime import date
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from banca.models import EventoFinancieroExterno, MovimientoBancario, PagoNeat
from configuracion.models import Banco, Producto
from patrimonio.models import (
    AbonoCuentaPorCobrar,
    Activo,
    CuentaPorCobrar,
    DetalleCuentaPorCobrar,
)
from patrimonio.services.cuentas_por_cobrar import (
    clave_fuente,
    proponer_renovacion,
    renovar_cuenta,
    texto_cobro,
)


class CuentaPorCobrarTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="cobros", password="secret")
        self.other_user = User.objects.create_user(username="cobros_other", password="secret")
        self.banco = Banco.objects.create(nombre="Banco Cobros")
        self.producto = Producto.objects.create(
            user=self.user, banco=self.banco, nombre="Visa personal", tipo="TDC"
        )
        self.client.force_login(self.user)

    def _movimiento(self, user=None, descripcion="Supermercado Ñuñoa 123", monto="100000", fecha=date(2026, 8, 10), hash_externo=None):
        user = user or self.user
        return MovimientoBancario.objects.create(
            user=user,
            producto=self.producto,
            fecha=fecha,
            descripcion=descripcion,
            monto=Decimal(monto),
            monto_clp=Decimal(monto),
            hash_externo=hash_externo or f"hash-{user.id}-{fecha}-{monto}-{descripcion}",
        )

    def test_cuenta_y_detalles_actualizan_activo_con_abonos_parciales(self):
        cuenta = CuentaPorCobrar.objects.create(
            user=self.user,
            nombre="Gastos compartidos Juan",
            descripcion="Compras que pagué por Juan",
        )
        detalle = DetalleCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            descripcion="Supermercado agosto",
            periodo=date(2026, 8, 1),
            fecha=date(2026, 8, 10),
            monto=Decimal("100000"),
        )
        AbonoCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            detalle=detalle,
            fecha=date(2026, 8, 20),
            monto=Decimal("35000"),
        )
        AbonoCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            fecha=date(2026, 8, 25),
            monto=Decimal("10000"),
            descripcion="Abono general",
        )

        cuenta.refresh_from_db()
        activo = cuenta.activo_patrimonial
        self.assertEqual(cuenta.total_cargado, Decimal("100000"))
        self.assertEqual(cuenta.total_abonado, Decimal("45000"))
        self.assertEqual(cuenta.saldo_pendiente, Decimal("55000"))
        self.assertEqual(activo.tipo, "CUENTA_COBRAR")
        self.assertEqual(activo.monto_clp, Decimal("55000"))
        self.assertFalse(activo.es_liquido)

    def test_asocia_tarjeta_y_renueva_mismo_comercio_con_monto_nuevo(self):
        anterior = self._movimiento()
        nuevo = self._movimiento(
            descripcion="SUPERMERCADO NUNOA 456",
            monto="125000",
            fecha=date(2026, 9, 9),
        )
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Juan")
        DetalleCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            descripcion="Compra mensual Juan",
            periodo=date(2026, 8, 1),
            fecha=anterior.fecha,
            monto=anterior.monto,
            origen="MOVIMIENTO",
            monto_manual=False,
            movimiento=anterior,
            clave_similitud="movimiento:%s:supermercado nunoa" % self.producto.id,
        )

        resultado = renovar_cuenta(cuenta, date(2026, 9, 1))

        self.assertEqual(resultado["creados"], 1)
        renovado = cuenta.detalles.get(periodo=date(2026, 9, 1))
        self.assertEqual(renovado.movimiento_id, nuevo.id)
        self.assertEqual(renovado.monto, Decimal("125000"))
        self.assertFalse(renovado.monto_manual)
        self.assertEqual(cuenta.saldo_pendiente, Decimal("225000"))

    def test_propuesta_busca_el_mes_futuro_sin_guardar_lineas(self):
        anterior = self._movimiento(
            descripcion="Arriendo departamento",
            monto="400000",
            fecha=date(2026, 9, 2),
        )
        futuro = self._movimiento(
            descripcion="ARRIENDO DEPARTAMENTO",
            monto="410000",
            fecha=date(2026, 10, 2),
        )
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Wilder")
        DetalleCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            descripcion="Arriendo",
            periodo=date(2026, 9, 1),
            fecha=anterior.fecha,
            monto=anterior.monto,
            origen="MOVIMIENTO",
            monto_manual=False,
            movimiento=anterior,
            clave_similitud=clave_fuente(anterior),
        )

        resultado = proponer_renovacion(cuenta, date(2026, 10, 1))

        self.assertEqual(len(resultado["propuestas"]), 1)
        self.assertEqual(resultado["propuestas"][0]["fuente"].id, futuro.id)
        self.assertEqual(resultado["propuestas"][0]["monto"], Decimal("410000"))
        self.assertEqual(cuenta.detalles.count(), 1)

    def test_propuesta_considera_cargo_anticipado_hasta_diez_dias_antes(self):
        anterior = EventoFinancieroExterno.objects.create(
            user=self.user,
            fuente="neat",
            tipo="PAGO",
            external_id="neat-arriendo-agosto-patron",
            periodo=date(2026, 8, 1),
            fecha_cargo=date(2026, 8, 1),
            moneda="CLP",
            monto_neto=Decimal("400000"),
        )
        PagoNeat.objects.create(
            evento=anterior,
            cuenta="arriendo",
            destinatario="Arrendatario",
            total_cargado=Decimal("400000"),
        )
        anticipado = EventoFinancieroExterno.objects.create(
            user=self.user,
            fuente="neat",
            tipo="PAGO",
            external_id="neat-arriendo-septiembre-anticipado",
            periodo=date(2026, 9, 1),
            fecha_cargo=date(2026, 8, 28),
            moneda="CLP",
            monto_neto=Decimal("405000"),
        )
        PagoNeat.objects.create(
            evento=anticipado,
            cuenta="arriendo",
            destinatario="Arrendatario",
            total_cargado=Decimal("405000"),
        )
        fuera_de_ventana = EventoFinancieroExterno.objects.create(
            user=self.user,
            fuente="neat",
            tipo="PAGO",
            external_id="neat-arriendo-septiembre-fuera-ventana",
            periodo=date(2026, 9, 1),
            fecha_cargo=date(2026, 8, 20),
            moneda="CLP",
            monto_neto=Decimal("406000"),
        )
        PagoNeat.objects.create(
            evento=fuera_de_ventana,
            cuenta="arriendo",
            destinatario="Arrendatario",
            total_cargado=Decimal("406000"),
        )
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Arriendo")
        DetalleCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            descripcion="Arriendo",
            periodo=date(2026, 8, 1),
            fecha=date(2026, 8, 1),
            monto=Decimal("400000"),
            origen="NEAT",
            monto_manual=False,
            evento_externo=anterior,
            clave_similitud=clave_fuente(anterior),
        )

        resultado = proponer_renovacion(cuenta, date(2026, 9, 1))

        self.assertEqual(len(resultado["propuestas"]), 1)
        self.assertEqual(resultado["propuestas"][0]["fuente"].id, anticipado.id)
        self.assertEqual(resultado["propuestas"][0]["monto"], Decimal("405000"))

    def test_linea_manual_no_se_inventa_en_la_renovacion(self):
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Viaje")
        DetalleCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            descripcion="Entrada manual",
            periodo=date(2026, 8, 1),
            fecha=date(2026, 8, 1),
            monto=Decimal("50000"),
        )

        resultado = renovar_cuenta(cuenta, date(2026, 9, 1))

        self.assertEqual(resultado["creados"], 0)
        self.assertEqual(cuenta.detalles.count(), 1)

    def test_renovacion_web_usa_el_primer_dia_del_mes(self):
        anterior = self._movimiento()
        nuevo = self._movimiento(
            descripcion="SUPERMERCADO NUNOA 456",
            monto="125000",
            fecha=date(2026, 9, 9),
        )
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Juan")
        DetalleCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            descripcion="Compra mensual Juan",
            periodo=date(2026, 8, 1),
            fecha=anterior.fecha,
            monto=anterior.monto,
            origen="MOVIMIENTO",
            monto_manual=False,
            movimiento=anterior,
            clave_similitud="movimiento:%s:supermercado nunoa" % self.producto.id,
        )

        response = self.client.post(
            reverse("renovar_cuenta_por_cobrar", args=[cuenta.id]),
            {"periodo": "2026-09"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(cuenta.detalles.filter(periodo=date(2026, 9, 1)).count(), 0)
        response = self.client.post(
            reverse("renovar_cuenta_por_cobrar", args=[cuenta.id]),
            {"periodo": "2026-09", "confirmar": "1"},
        )
        self.assertEqual(response.status_code, 302)
        renovado = cuenta.detalles.get(periodo=date(2026, 9, 1))
        self.assertEqual(renovado.movimiento_id, nuevo.id)
        self.assertEqual(renovado.monto, Decimal("125000"))

    def test_neat_se_asocia_y_no_permite_moneda_distinta(self):
        evento = EventoFinancieroExterno.objects.create(
            user=self.user,
            fuente="neat",
            tipo="PAGO",
            external_id="neat-001",
            periodo=date(2026, 8, 1),
            fecha_cargo=date(2026, 8, 12),
            moneda="CLP",
            monto_neto=Decimal("88000"),
        )
        PagoNeat.objects.create(
            evento=evento,
            destinatario="Juan Pérez",
            tarjeta_ultimos4="0776",
            total_cargado=Decimal("88000"),
        )
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Neat Juan")

        response = self.client.post(
            reverse("agregar_detalle_cuenta_por_cobrar"),
            {
                "cuenta_id": cuenta.id,
                "fuente": f"neat:{evento.id}",
                "descripcion": "Pago Neat de Juan",
                "periodo": "2026-08",
                "fecha": "2026-08-12",
            },
        )

        self.assertEqual(response.status_code, 302)
        detalle = cuenta.detalles.get()
        self.assertEqual(detalle.origen, "NEAT")
        self.assertEqual(detalle.monto, Decimal("88000"))
        self.assertEqual(cuenta.activo_patrimonial.monto_clp, Decimal("88000"))

        usd = CuentaPorCobrar.objects.create(user=self.user, nombre="Dólares", moneda="USD")
        self.client.post(
            reverse("agregar_detalle_cuenta_por_cobrar"),
            {
                "cuenta_id": usd.id,
                "fuente": f"neat:{evento.id}",
                "descripcion": "No corresponde",
            },
        )
        self.assertFalse(usd.detalles.exists())

    def test_editar_linea_manual_la_asocia_a_neat_y_toma_su_monto(self):
        evento = EventoFinancieroExterno.objects.create(
            user=self.user,
            fuente="neat",
            tipo="PAGO",
            external_id="neat-editar-001",
            periodo=date(2026, 9, 1),
            fecha_cargo=date(2026, 9, 5),
            moneda="CLP",
            monto_neto=Decimal("405000"),
        )
        PagoNeat.objects.create(evento=evento, cuenta="arriendo", total_cargado=Decimal("405000"))
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Wilder")
        detalle = DetalleCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            descripcion="Arriendo",
            periodo=date(2026, 9, 1),
            fecha=date(2026, 9, 2),
            monto=Decimal("400000"),
        )

        response = self.client.post(
            reverse("editar_detalle_cuenta_por_cobrar", args=[detalle.id]),
            {
                "fuente": f"neat:{evento.id}",
                "descripcion": "Arriendo",
                "periodo": "2026-09",
                "fecha": "2026-09-02",
                "monto": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        detalle.refresh_from_db()
        self.assertEqual(detalle.evento_externo_id, evento.id)
        self.assertEqual(detalle.monto, Decimal("405000"))
        self.assertEqual(detalle.fecha, date(2026, 9, 5))
        self.assertEqual(detalle.origen, "NEAT")
        self.assertFalse(detalle.monto_manual)

    def test_cobro_provisional_se_reemplaza_por_movimiento_sin_duplicar(self):
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Fabiola")
        response = self.client.post(
            reverse("agregar_detalle_cuenta_por_cobrar"),
            {
                "cuenta_id": cuenta.pk,
                "descripcion": "Comunidad Feliz septiembre",
                "periodo": "2026-09",
                "fecha": "2026-09-09",
                "monto": "85000",
                "provisional": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        detalle = cuenta.detalles.get()
        self.assertTrue(detalle.provisional)
        self.assertTrue(detalle.monto_manual)
        self.assertIsNone(detalle.movimiento_id)
        pantalla = self.client.get(reverse("cuentas_por_cobrar"), {"periodo": "2026-09"})
        self.assertContains(pantalla, "Provisional · falta estado")

        movimiento_real = self._movimiento(
            descripcion="COMUNIDAD FELIZ SPA",
            monto="83240",
            fecha=date(2026, 9, 20),
            hash_externo="comunidad-septiembre-real",
        )
        response = self.client.post(
            reverse("editar_detalle_cuenta_por_cobrar", args=[detalle.pk]),
            {
                "fuente": f"movimiento:{movimiento_real.pk}",
                "descripcion": "Comunidad Feliz septiembre",
                "periodo": "2026-09",
                "fecha": "2026-09-09",
                "monto": "85000",
                "provisional": "1",
            },
        )
        self.assertEqual(response.status_code, 302)
        detalle.refresh_from_db()
        self.assertEqual(cuenta.detalles.count(), 1)
        self.assertFalse(detalle.provisional)
        self.assertFalse(detalle.monto_manual)
        self.assertEqual(detalle.movimiento_id, movimiento_real.pk)
        self.assertEqual(detalle.monto, Decimal("83240"))

    def test_editar_linea_no_reutiliza_neat_de_otro_mes_y_deja_formulario_abierto(self):
        evento = EventoFinancieroExterno.objects.create(
            user=self.user,
            fuente="neat",
            tipo="PAGO",
            external_id="neat-editar-duplicado-001",
            periodo=date(2026, 8, 1),
            fecha_cargo=date(2026, 8, 28),
            moneda="CLP",
            monto_neto=Decimal("400000"),
        )
        PagoNeat.objects.create(evento=evento, cuenta="arriendo", total_cargado=Decimal("400000"))
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Wilder")
        DetalleCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            descripcion="Arriendo",
            periodo=date(2026, 8, 1),
            fecha=date(2026, 8, 28),
            monto=Decimal("400000"),
            origen="NEAT",
            monto_manual=False,
            evento_externo=evento,
            clave_similitud=clave_fuente(evento),
        )
        septiembre = DetalleCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            descripcion="Arriendo",
            periodo=date(2026, 9, 1),
            fecha=date(2026, 9, 2),
            monto=Decimal("400000"),
        )

        response = self.client.post(
            reverse("editar_detalle_cuenta_por_cobrar", args=[septiembre.id]),
            {
                "fuente": f"neat:{evento.id}",
                "descripcion": "Arriendo",
                "periodo": "2026-09",
                "fecha": "2026-09-02",
                "monto": "400000",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(f"periodo=2026-09&editar={septiembre.id}", response.url)
        septiembre.refresh_from_db()
        self.assertIsNone(septiembre.evento_externo_id)
        self.assertEqual(septiembre.origen, "MANUAL")
        formulario = self.client.get(response.url.split("#")[0])
        self.assertContains(formulario, "esa transacción ya está asociada")
        self.assertContains(formulario, "Editar línea")

    def test_editar_linea_no_expone_fuente_de_otro_usuario(self):
        evento = EventoFinancieroExterno.objects.create(
            user=self.other_user,
            fuente="neat",
            tipo="PAGO",
            external_id="neat-otro-001",
            fecha_cargo=date(2026, 9, 5),
            moneda="CLP",
            monto_neto=Decimal("405000"),
        )
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Wilder")
        detalle = DetalleCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            descripcion="Arriendo",
            periodo=date(2026, 9, 1),
            fecha=date(2026, 9, 2),
            monto=Decimal("400000"),
        )

        response = self.client.post(
            reverse("editar_detalle_cuenta_por_cobrar", args=[detalle.id]),
            {"fuente": f"neat:{evento.id}", "descripcion": "Arriendo"},
        )

        self.assertEqual(response.status_code, 404)
        detalle.refresh_from_db()
        self.assertIsNone(detalle.evento_externo_id)
        self.assertEqual(detalle.monto, Decimal("400000"))

    def test_borrar_abono_no_permite_acceder_a_otra_cuenta_usuario(self):
        otra_cuenta = CuentaPorCobrar.objects.create(user=self.other_user, nombre="Otra persona")
        otro_abono = AbonoCuentaPorCobrar.objects.create(
            cuenta=otra_cuenta,
            fecha=date(2026, 9, 2),
            monto=Decimal("5000"),
        )

        response = self.client.post(
            reverse("borrar_abono_cuenta_por_cobrar", args=[otro_abono.id])
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(AbonoCuentaPorCobrar.objects.filter(pk=otro_abono.id).exists())

    def test_fuentes_y_activos_estan_aislados_por_usuario(self):
        other_movement = self._movimiento(
            user=self.other_user,
            descripcion="Compra privada de otra persona",
            hash_externo="other-hash",
        )
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Cuenta propia")

        response = self.client.get(reverse("cuentas_por_cobrar"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, other_movement.descripcion)
        forbidden = self.client.post(
            reverse("agregar_detalle_cuenta_por_cobrar"),
            {
                "cuenta_id": cuenta.id,
                "fuente": f"movimiento:{other_movement.id}",
                "descripcion": "No autorizado",
            },
        )
        self.assertEqual(forbidden.status_code, 404)

    def test_fuentes_neat_muestran_fecha_alias_y_no_inventan_hoy(self):
        antiguo = EventoFinancieroExterno.objects.create(
            user=self.user,
            fuente="neat",
            tipo="PAGO",
            external_id="neat-antiguo-sin-fecha",
            moneda="CLP",
            monto_neto=Decimal("53641"),
            metadata={"alias": "Luz 1024", "dia_de_pago": "2026-06-04T03:39:36.000Z"},
        )
        PagoNeat.objects.create(evento=antiguo, cuenta="ENEL")
        actual = EventoFinancieroExterno.objects.create(
            user=self.user,
            fuente="neat",
            tipo="PAGO",
            external_id="neat-entel-septiembre",
            fecha_cargo=date(2026, 9, 2),
            periodo=date(2026, 9, 1),
            moneda="CLP",
            monto_neto=Decimal("26269"),
            metadata={"alias": "Entel Fabiana"},
        )
        PagoNeat.objects.create(evento=actual, cuenta="Entel PCS")
        CuentaPorCobrar.objects.create(user=self.user, nombre="Servicios")

        response = self.client.get(
            reverse("cuentas_por_cobrar") + "?periodo=2026-09"
        )

        self.assertNotContains(response, "NeatPagos · 04/06/2026 · ENEL · Luz 1024 · $53,641")
        self.assertContains(response, "NeatPagos · 02/09/2026 · Entel PCS · Entel Fabiana · $26,269")

    def test_fuentes_neat_incluyen_hasta_diez_dias_antes_del_mes(self):
        dentro = EventoFinancieroExterno.objects.create(
            user=self.user,
            fuente="neat",
            tipo="PAGO",
            external_id="neat-ventana-diez-dias",
            periodo=date(2026, 8, 1),
            fecha_cargo=date(2026, 8, 22),
            moneda="CLP",
            monto_neto=Decimal("400000"),
        )
        PagoNeat.objects.create(evento=dentro, cuenta="Arriendo", total_cargado=Decimal("400000"))
        fuera = EventoFinancieroExterno.objects.create(
            user=self.user,
            fuente="neat",
            tipo="PAGO",
            external_id="neat-ventana-once-dias",
            periodo=date(2026, 8, 1),
            fecha_cargo=date(2026, 8, 21),
            moneda="CLP",
            monto_neto=Decimal("410000"),
        )
        PagoNeat.objects.create(evento=fuera, cuenta="Arriendo", total_cargado=Decimal("410000"))
        CuentaPorCobrar.objects.create(user=self.user, nombre="Arriendos")

        response = self.client.get(reverse("cuentas_por_cobrar") + "?periodo=2026-09")

        self.assertContains(response, "22/08/2026")
        self.assertNotContains(response, "21/08/2026")

    def test_modelo_rechaza_fuente_o_abono_de_otra_cuenta_usuario(self):
        otra_cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Otra cuenta")
        detalle = DetalleCuentaPorCobrar.objects.create(
            cuenta=otra_cuenta,
            descripcion="Línea ajena",
            periodo=date(2026, 9, 1),
            fecha=date(2026, 9, 1),
            monto=Decimal("1000"),
        )
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Cuenta propia")

        with self.assertRaises(ValidationError):
            AbonoCuentaPorCobrar.objects.create(
                cuenta=cuenta,
                detalle=detalle,
                fecha=date(2026, 9, 2),
                monto=Decimal("100"),
            )

        other_movement = self._movimiento(
            user=self.other_user, descripcion="Movimiento ajeno", hash_externo="cross-user"
        )
        with self.assertRaises(ValidationError):
            DetalleCuentaPorCobrar.objects.create(
                cuenta=cuenta,
                descripcion="Fuente ajena",
                periodo=date(2026, 9, 1),
                fecha=other_movement.fecha,
                monto=other_movement.monto,
                origen="MOVIMIENTO",
                monto_manual=False,
                movimiento=other_movement,
            )

    def test_pagina_muestra_texto_copiable_y_cuenta_en_activos(self):
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Casa")
        movimiento = self._movimiento(
            descripcion="Supermercado para Casa", monto="95000", fecha=date(2026, 9, 4)
        )
        DetalleCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            descripcion="Cuenta de luz",
            periodo=date(2026, 9, 1),
            fecha=date(2026, 9, 3),
            monto=Decimal("42000"),
        )

        cobros = self.client.get(reverse("cuentas_por_cobrar"), {"periodo": "2026-09"})
        activos = self.client.get(reverse("activos"))

        self.assertContains(cobros, "Texto para cobrar")
        self.assertContains(cobros, "Total pendiente: $42.000")
        self.assertContains(cobros, "Proponer mes")
        self.assertContains(cobros, 'type="search"')
        self.assertContains(cobros, "cobro-source-search")
        self.assertNotContains(cobros, '<select name="fuente"')
        self.assertContains(cobros, f'data-value="movimiento:{movimiento.id}"')
        self.assertContains(cobros, "Monto tomado de la transacción seleccionada.")
        self.assertContains(cobros, "amount.required = !asociado")
        self.assertContains(cobros, "Editar")
        self.assertContains(activos, "Casa")
        self.assertContains(activos, "Cuenta por cobrar")

    def test_texto_cobro_resume_meses_anteriores_en_una_linea(self):
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Wilder")
        DetalleCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            descripcion="Arriendo",
            periodo=date(2026, 8, 1),
            fecha=date(2026, 8, 28),
            monto=Decimal("400000"),
        )
        DetalleCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            descripcion="Arriendo",
            periodo=date(2026, 9, 1),
            fecha=date(2026, 9, 2),
            monto=Decimal("405000"),
        )

        texto = texto_cobro(cuenta, date(2026, 9, 1))

        self.assertIn("- Arriendo: $405.000", texto)
        self.assertIn("- Saldo pendiente anterior: $400.000", texto)
        self.assertNotIn("09/2026", texto)
        self.assertNotIn("Arriendo (08/2026)", texto)

    def test_tabla_muestra_solo_el_periodo_seleccionado_y_conserva_total(self):
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Wilder")
        DetalleCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            descripcion="Arriendo agosto",
            periodo=date(2026, 8, 1),
            fecha=date(2026, 8, 28),
            monto=Decimal("400000"),
        )
        DetalleCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            descripcion="Arriendo septiembre",
            periodo=date(2026, 9, 1),
            fecha=date(2026, 9, 2),
            monto=Decimal("410000"),
        )

        response = self.client.get(
            reverse("cuentas_por_cobrar") + "?periodo=2026-09"
        )

        self.assertContains(response, 'data-periodo-visible="2026-09"')
        self.assertContains(response, 'data-detalle-periodo="2026-09"')
        self.assertNotContains(response, 'data-detalle-periodo="2026-08"')
        self.assertContains(response, "Saldo pendiente anterior: $400.000")
        self.assertNotContains(response, "Arriendo agosto (08/2026)")
        self.assertContains(response, "$810.000")

        agosto = self.client.get(
            reverse("cuentas_por_cobrar") + "?periodo=2026-08"
        )
        self.assertContains(agosto, 'data-periodo-visible="2026-08"')
        self.assertContains(agosto, "Arriendo agosto")

    def test_renovacion_web_primero_propone_y_luego_confirma(self):
        anterior = self._movimiento(
            descripcion="Arriendo departamento",
            monto="400000",
            fecha=date(2026, 9, 2),
        )
        futuro = self._movimiento(
            descripcion="ARRIENDO DEPARTAMENTO",
            monto="410000",
            fecha=date(2026, 10, 2),
        )
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Wilder")
        DetalleCuentaPorCobrar.objects.create(
            cuenta=cuenta,
            descripcion="Arriendo",
            periodo=date(2026, 9, 1),
            fecha=anterior.fecha,
            monto=anterior.monto,
            origen="MOVIMIENTO",
            monto_manual=False,
            movimiento=anterior,
            clave_similitud=clave_fuente(anterior),
        )

        response = self.client.post(
            reverse("renovar_cuenta_por_cobrar", args=[cuenta.id]),
            {"periodo": "2026-10"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(cuenta.detalles.count(), 1)
        propuesta = self.client.get(response.url)
        self.assertContains(propuesta, "Propuesta para 10/2026")
        self.assertContains(propuesta, "Confirmar y agregar")
        self.assertContains(propuesta, 'data-periodo-visible="2026-10"')

        confirmado = self.client.post(
            reverse("renovar_cuenta_por_cobrar", args=[cuenta.id]),
            {"periodo": "2026-10", "confirmar": "1"},
        )

        self.assertEqual(confirmado.status_code, 302)
        renovado = cuenta.detalles.get(periodo=date(2026, 10, 1))
        self.assertEqual(renovado.movimiento_id, futuro.id)
        self.assertEqual(renovado.monto, Decimal("410000"))

    def test_renovacion_web_aisla_cuenta_de_otro_usuario(self):
        otra_cuenta = CuentaPorCobrar.objects.create(user=self.other_user, nombre="Otra")

        response = self.client.post(
            reverse("renovar_cuenta_por_cobrar", args=[otra_cuenta.id]),
            {"periodo": "2026-10"},
        )

        self.assertEqual(response.status_code, 404)

    def test_formularios_permiten_linea_manual_abono_y_archivado(self):
        response = self.client.post(
            reverse("crear_cuenta_por_cobrar"),
            {"nombre": "Reembolso oficina", "descripcion": "Gastos de septiembre", "moneda": "CLP"},
        )
        self.assertEqual(response.status_code, 302)
        cuenta = CuentaPorCobrar.objects.get(nombre="Reembolso oficina")
        self.client.post(
            reverse("agregar_detalle_cuenta_por_cobrar"),
            {
                "cuenta_id": cuenta.id,
                "descripcion": "Taxi compartido",
                "monto": "18000",
                "fecha": "2026-09-05",
                "periodo": "2026-09",
            },
        )
        detalle = cuenta.detalles.get()
        self.client.post(
            reverse("registrar_abono_cuenta_por_cobrar"),
            {
                "cuenta_id": cuenta.id,
                "detalle_id": detalle.id,
                "monto": "5000",
                "fecha": "2026-09-20",
                "descripcion": "Abono recibido",
            },
        )
        abono = cuenta.abonos.get()
        pagina = self.client.get(reverse("cuentas_por_cobrar"))
        self.assertContains(pagina, "Quitar abono")
        self.assertContains(pagina, reverse("borrar_abono_cuenta_por_cobrar", args=[abono.id]))
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.saldo_pendiente, Decimal("13000"))
        self.assertEqual(cuenta.activo_patrimonial.monto_clp, Decimal("13000"))

        response = self.client.post(
            reverse("borrar_abono_cuenta_por_cobrar", args=[abono.id])
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AbonoCuentaPorCobrar.objects.filter(pk=abono.id).exists())
        cuenta.refresh_from_db()
        self.assertEqual(cuenta.saldo_pendiente, Decimal("18000"))
        self.assertEqual(cuenta.activo_patrimonial.monto_clp, Decimal("18000"))

        self.client.post(reverse("alternar_cuenta_por_cobrar", args=[cuenta.id]))
        cuenta.refresh_from_db()
        self.assertFalse(cuenta.activo)
        self.assertFalse(cuenta.activo_patrimonial.activo)
