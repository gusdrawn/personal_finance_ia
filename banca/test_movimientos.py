from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from banca.models import (
    CategoriaTematica, ConciliacionEventoExterno, EventoFinancieroExterno,
    MovimientoBancario,
)
from banca.forms import ConfiguracionBancaForm
from banca.management.commands.seed_categorias_banca import sembrar
from banca.services.presupuesto import detalle_promedios_categoria
from banca.services.clasificacion_ia import _aplicar
from configuracion.models import Banco, Producto
from gastos.services import resumen_gastos
from patrimonio.models import AbonoCuentaPorCobrar, CuentaPorCobrar
from prestamos.models import HistorialPrestamo, Prestamo
from banca.views import _avance_bicicletas


class MovimientosViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="movimientos_user", password="x")
        self.other = User.objects.create_user(username="movimientos_other", password="x")
        self.bank = Banco.objects.create(nombre="Banco Movimientos")
        self.product = Producto.objects.create(
            user=self.user, banco=self.bank, nombre="Visa ••••4321", tipo="TDC", ultimos4="4321"
        )
        self.food = CategoriaTematica.objects.create(
            user=self.user, nombre="Supermercado", aplica_a="EGRESO", origen="USUARIO"
        )
        self.transport = CategoriaTematica.objects.create(
            user=self.user, nombre="Transporte", aplica_a="EGRESO", origen="USUARIO"
        )

    def movement(self, description, category, hash_value, amount="12000"):
        return MovimientoBancario.objects.create(
            user=self.user,
            producto=self.product,
            fecha=date(2026, 8, 10),
            descripcion=description,
            monto=Decimal(amount),
            monto_clp=Decimal(amount),
            categoria=category,
            estado="FACTURADO",
            origen="PDF",
            hash_externo=hash_value,
        )

    def test_busqueda_muestra_solo_la_coincidencia_y_grafica(self):
        self.movement("Supermercado Central", self.food, "search-food")
        self.movement("Metro Santiago", self.transport, "search-transport")
        self.client.force_login(self.user)

        response = self.client.get(reverse("banca_movimientos"), {"q": "Central"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Supermercado Central")
        self.assertNotContains(response, "Metro Santiago")
        self.assertContains(response, "movimientos-categorias-chart")
        self.assertContains(response, f'data-categoria="{self.food.pk}"')
        self.assertContains(response, "Haz clic en una categoría para filtrar")
        self.assertContains(response, "filtros-movimientos').submit()")

    def test_tabla_muestra_direccion_y_tipo_del_movimiento(self):
        self.movement("Compra enviada", self.food, "direction-out", "12000")
        incoming = self.movement("Transferencia recibida", None, "direction-in", "-45000")
        incoming.naturaleza = "TRANSFERENCIA"
        incoming.save(update_fields=["naturaleza"])
        self.client.force_login(self.user)

        response = self.client.get(reverse("banca_movimientos"))

        self.assertContains(response, "<th class=\"p-3\">Movimiento</th>")
        self.assertContains(response, "↓ Cargo")
        self.assertContains(response, "Gasto")
        self.assertContains(response, "↑ Abono")
        self.assertContains(response, "Transferencia")
        self.assertNotContains(response, "$-45")

    def test_estado_cuenta_corriente_y_filtros_de_tarjeta(self):
        cuenta = Producto.objects.create(user=self.user, banco=self.bank, nombre="Cuenta", tipo="CUENTA")
        registrado = self.movement("Abono cuenta corriente", None, "cc-registrado", "-45000")
        registrado.producto = cuenta
        registrado.save(update_fields=["producto"])
        tarjeta = self.movement("Compra tarjeta", self.food, "tdc-facturado")
        self.client.force_login(self.user)
        vista = reverse("banca_movimientos")
        response = self.client.get(vista)
        self.assertContains(response, "Registrado en cuenta")
        self.assertContains(response, "Facturado")
        self.assertContains(self.client.get(vista, {"estado": "REGISTRADO_CUENTA"}), "Abono cuenta corriente")
        self.assertNotContains(self.client.get(vista, {"estado": "FACTURADO"}), "Abono cuenta corriente")
        self.assertContains(self.client.get(vista, {"estado": "FACTURADO"}), tarjeta.descripcion)

    def test_pendiente_cartola_incluye_apple_pay_sin_duplicar(self):
        provisional = self.movement("Pago iPhone", self.food, "apple-provisional")
        provisional.estado, provisional.origen = "PROVISIONAL", "APPLE_PAY"
        provisional.save(update_fields=["estado", "origen"])
        sin_facturar = self.movement("Compra banco", self.food, "banco-pendiente")
        sin_facturar.estado = "NO_FACTURADO"
        sin_facturar.save(update_fields=["estado"])
        self.client.force_login(self.user)
        response = self.client.get(reverse("banca_movimientos"), {"estado": "PENDIENTE_CARTOLA"})
        self.assertEqual(response.context["total_movimientos"], 2)
        self.assertContains(response, "Pago iPhone")
        self.assertContains(response, "Compra banco")
        solo_apple = self.client.get(reverse("banca_movimientos"), {"estado": "PROVISIONAL", "origen": "APPLE_PAY"})
        self.assertEqual(solo_apple.context["total_movimientos"], 1)

    def test_bicicletas_se_siembra_y_vincula_como_transferencia(self):
        self.assertGreater(sembrar(self.user), 0)
        self.assertEqual(sembrar(self.user), 0)
        categoria = CategoriaTematica.objects.get(user=self.user, nombre="Bicicletas")
        self.assertEqual(categoria.aplica_a, "AMBOS")
        prestamo = Prestamo.objects.create(user=self.user, nombre="Bici Ana", tipo="TERCEROS", monto_total=Decimal("20000"))
        movimiento = self.movement("Pago Ana", None, "bici-ana")
        self.client.force_login(self.user)
        self.client.post(reverse("banca_asignar_prestamo", args=[movimiento.pk]), {"prestamo": prestamo.pk})
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.categoria, categoria)
        self.assertEqual(movimiento.naturaleza, "TRANSFERENCIA")
        self.assertFalse(movimiento.contabiliza)
        self.assertFalse(CategoriaTematica.objects.filter(user=self.other, nombre="Bicicletas").exists())

    def test_categoria_bicicletas_manual_no_cuenta_como_gasto_y_se_puede_revertir(self):
        categoria = CategoriaTematica.objects.create(user=self.user, nombre="Bicicletas", aplica_a="AMBOS")
        movimiento = self.movement("Movimiento bicicleta", None, "bici-manual")
        self.client.force_login(self.user)
        self.client.post(reverse("banca_reclasificar", args=[movimiento.pk]), {"categoria": categoria.pk})
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.naturaleza, "TRANSFERENCIA")
        self.assertFalse(movimiento.contabiliza)
        self.client.post(reverse("banca_reclasificar", args=[movimiento.pk]), {"categoria": self.food.pk})
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.naturaleza, "OPERACIONAL")
        self.assertTrue(movimiento.contabiliza)

    def test_sugerencia_automatica_de_bicicleta_requiere_revision(self):
        categoria = CategoriaTematica.objects.create(user=self.user, nombre="Bicicletas", aplica_a="AMBOS")
        movimiento = self.movement("Posible bicicleta", None, "bici-sugerida")
        _aplicar(movimiento, categoria, "IA")
        movimiento.refresh_from_db()
        self.assertTrue(movimiento.por_revisar)
        self.assertFalse(movimiento.contabiliza)

    def test_promedio_categoria_incluye_mes_sin_gasto_y_aisla_usuario(self):
        movimiento = self.movement("Compra agosto", self.food, "prom-agosto", "9000")
        movimiento.fecha = date(2026, 8, 10)
        movimiento.save(update_fields=["fecha"])
        otro_producto = Producto.objects.create(user=self.other, banco=self.bank, nombre="Otra", tipo="TDC")
        otra_categoria = CategoriaTematica.objects.create(user=self.other, nombre="Supermercado", aplica_a="EGRESO")
        MovimientoBancario.objects.create(user=self.other, producto=otro_producto, fecha=date(2026, 8, 10), descripcion="Ajeno", monto=900000, monto_clp=900000, categoria=otra_categoria, hash_externo="prom-ajeno")
        futuro = self.movement("Compra octubre", self.food, "prom-futuro", "50000")
        futuro.fecha = date(2026, 10, 1)
        futuro.save(update_fields=["fecha"])
        filas = detalle_promedios_categoria(self.user, 3, date(2026, 9, 29))
        comida = next(f for f in filas if f["categoria"] == self.food)
        self.assertEqual([m["monto"] for m in comida["meses"]], [Decimal("0"), Decimal("0"), Decimal("9000")])
        self.assertEqual(comida["promedio"], Decimal("3000"))
        self.assertEqual(comida["actual"], Decimal("0"))

    def test_avance_bicicletas_usa_monto_y_movimientos_del_mes_consultado(self):
        prestamo = Prestamo.objects.create(user=self.user, nombre="Bici histórica", tipo="TERCEROS", monto_total=Decimal("8000000"))
        HistorialPrestamo.objects.create(prestamo=prestamo, fecha=date(2026, 1, 1), monto_total=Decimal("20000000"))
        HistorialPrestamo.objects.create(prestamo=prestamo, fecha=date(2026, 9, 10), monto_total=Decimal("8000000"))
        cargo = self.movement("Bici cargo", None, "bici-cargo", "20000000")
        cargo.prestamo = prestamo
        cargo.save(update_fields=["prestamo"])
        abono = self.movement("Bici abono", None, "bici-abono", "-12000000")
        abono.fecha = date(2026, 9, 10)
        abono.prestamo = prestamo
        abono.save(update_fields=["fecha", "prestamo"])
        agosto = _avance_bicicletas(self.user, date(2026, 8, 31))[0]
        septiembre = _avance_bicicletas(self.user, date(2026, 9, 30))[0]
        self.assertEqual((agosto["total"], agosto["retornado"]), (Decimal("20000000"), Decimal("0")))
        self.assertEqual((septiembre["total"], septiembre["retornado"]), (Decimal("8000000"), Decimal("12000000")))
        self.assertEqual((agosto["vinculado"], septiembre["vinculado"]), (Decimal("20000000"), Decimal("8000000")))

    def test_avance_anterior_al_primer_registro_no_inventa_monto(self):
        prestamo = Prestamo.objects.create(user=self.user, nombre="Bici sin pasado", tipo="TERCEROS", monto_total=Decimal("8000000"))
        Prestamo.objects.filter(pk=prestamo.pk).update(created_at=datetime(2026, 1, 1, tzinfo=dt_timezone.utc))
        HistorialPrestamo.objects.create(prestamo=prestamo, fecha=date(2026, 9, 1), monto_total=Decimal("8000000"))
        agosto = _avance_bicicletas(self.user, date(2026, 8, 31))[0]
        self.assertIsNone(agosto["total"])

    def test_categorias_permiten_definir_si_aplican_a_ingreso(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse("banca_categorias"), {
            "nombre": "Honorarios",
            "aplica_a": "INGRESO",
            "esencialidad_defecto": "FIJO",
            "orden": 1,
            "activa": "on",
        })

        self.assertRedirects(response, reverse("banca_categorias"))
        categoria = CategoriaTematica.objects.get(user=self.user, nombre="Honorarios")
        self.assertEqual(categoria.aplica_a, "INGRESO")
        self.assertContains(self.client.get(reverse("banca_categorias")), "Honorarios")
        self.assertContains(self.client.get(reverse("banca_categorias")), "Ingreso")

    def test_filtro_categoria_restringe_tabla_sin_perder_contexto_de_grafica(self):
        self.movement("Supermercado Central", self.food, "filter-food")
        self.movement("Metro Santiago", self.transport, "filter-transport")
        MovimientoBancario.objects.create(
            user=self.user,
            producto=self.product,
            fecha=date(2026, 8, 11),
            descripcion="Reembolso Supermercado",
            monto=Decimal("-2000"),
            monto_clp=Decimal("-2000"),
            categoria=self.food,
            estado="FACTURADO",
            origen="PDF",
            hash_externo="filter-refund",
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("banca_movimientos"), {"categoria": str(self.food.pk)}
        )

        self.assertContains(response, "Supermercado Central")
        self.assertNotContains(response, "Metro Santiago")
        self.assertContains(response, '"nombre": "Transporte"')
        chart_totals = {row["nombre"]: row["total"] for row in response.context["categoria_chart"]}
        self.assertEqual(chart_totals["Supermercado"], "10000")

    def test_movimientos_de_otro_usuario_no_aparecen_en_busqueda_ni_grafica(self):
        MovimientoBancario.objects.create(
            user=self.other,
            producto=Producto.objects.create(
                user=self.other, banco=self.bank, nombre="Otra tarjeta", tipo="TDC"
            ),
            fecha=date(2026, 8, 10),
            descripcion="Movimiento privado",
            monto=Decimal("99999"),
            monto_clp=Decimal("99999"),
            categoria=CategoriaTematica.objects.create(
                user=self.other, nombre="Privada", aplica_a="EGRESO"
            ),
            estado="FACTURADO",
            origen="PDF",
            hash_externo="other-user-movement",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("banca_movimientos"), {"q": "Movimiento"})

        self.assertNotContains(response, "Movimiento privado")
        self.assertNotContains(response, "Privada")

    def test_subpagina_muestra_solo_cargos_facturados_y_permita_clasificar(self):
        self.movement("Compra facturada", self.food, "facturada-vista")
        pagada = self.movement("Compra pagada", self.transport, "pagada-vista")
        pagada.estado = "PAGADO"
        pagada.save(update_fields=["estado"])
        pendiente = self.movement("Compra pendiente", None, "pendiente-vista")
        pendiente.estado = "NO_FACTURADO"
        pendiente.save(update_fields=["estado"])
        self.client.force_login(self.user)

        response = self.client.get(reverse("gastos_movimientos"), {
            "fecha_desde": "2026-08-01", "fecha_hasta": "2026-08-31"
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Compra facturada")
        self.assertContains(response, "Compra pagada")
        self.assertNotContains(response, "Compra pendiente")
        self.assertContains(response, "Clasificar pendientes con IA")
        self.assertContains(response, "Supermercado")
        self.assertEqual(sum((Decimal(row["total"]) for row in response.context["categoria_chart"]),
                         Decimal("0")), Decimal("24000"))

        no_facturados = self.client.get(reverse("gastos_movimientos"), {
            "estado": "NO_FACTURADO", "fecha_desde": "2026-08-01", "fecha_hasta": "2026-08-31"
        })
        self.assertContains(no_facturados, "Compra pendiente")
        self.assertNotContains(no_facturados, "Compra facturada")
        self.assertNotContains(no_facturados, "Compra pagada")

    def test_pendientes_de_gastos_incluye_abonos_y_acciones_de_revision(self):
        abono = MovimientoBancario.objects.create(
            user=self.user,
            producto=self.product,
            fecha=date(2026, 8, 10),
            descripcion="Transferencia sin reconocer",
            monto=Decimal("-200000"),
            monto_clp=Decimal("-200000"),
            estado="FACTURADO",
            origen="PDF",
            por_revisar=True,
            hash_externo="pending-credit-gastos",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("gastos_movimientos"), {
            "pendientes": "1",
            "fecha_desde": "2026-08-01",
            "fecha_hasta": "2026-08-31",
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Transferencia sin reconocer")
        self.assertContains(response, "Otra transferencia (sin categoría)")
        self.assertContains(response, "Transferencias entre mis cuentas")

    def test_clasificacion_ia_por_movimiento_aisla_usuario(self):
        otro_producto = Producto.objects.create(user=self.other, banco=self.bank, nombre="Ajena", tipo="TDC")
        ajeno = MovimientoBancario.objects.create(
            user=self.other, producto=otro_producto, fecha=date(2026, 8, 10),
            descripcion="Privada", monto=Decimal("100"), monto_clp=Decimal("100"),
            estado="NO_FACTURADO", hash_externo="ia-ajena",
        )
        self.client.force_login(self.user)
        response = self.client.post(reverse("banca_recalcular_clasificaciones"), {
            "scope": "movimiento", "movimiento_id": ajeno.pk,
        })
        self.assertEqual(response.status_code, 404)
        with patch("banca.views.clasificar_pendientes", return_value={
            "ia": 0, "regla": 0, "sin_resolver": 0,
        }) as clasificar:
            response = self.client.post(reverse("banca_recalcular_clasificaciones"), {
                "scope": "pendientes",
            })
        self.assertEqual(response.status_code, 302)
        clasificar.assert_called_once_with(self.user)

    def test_presupuesto_configurable_rechaza_montos_negativos(self):
        datos = {
            "ventana_meses_ingreso": 3, "umbral_alerta_ocio": "0.80",
            "presupuesto_mensual_clp": "-1", "tolerancia_comision_bicicleta": "0.030",
            "ia_base_url": "https://opencode.ai/zen/go/v1/", "modelo_ia": "glm-5.3-flash",
        }
        self.assertFalse(ConfiguracionBancaForm(datos).is_valid())
        datos["presupuesto_mensual_clp"] = "0"
        self.assertTrue(ConfiguracionBancaForm(datos).is_valid())

    def test_umbral_visual_en_porcentaje_se_guarda_como_fraccion(self):
        form = ConfiguracionBancaForm({
            "ventana_meses_ingreso": "3", "umbral_alerta_ocio_pct": "90",
            "presupuesto_mensual_clp": "200000", "tolerancia_comision_bicicleta": "0.030",
            "ia_base_url": "https://example.com/v1", "modelo_ia": "modelo",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save(commit=False).umbral_alerta_ocio, Decimal("0.90"))

    def test_pago_neat_se_reclasifica_sin_cambiar_cargo_y_aisla_usuario(self):
        movimiento = self.movement("NEAT PAGOS", self.food, "neat-cargo-vista")
        evento = EventoFinancieroExterno.objects.create(
            user=self.user, fuente="neat", tipo="PAGO", external_id="neat-detalle-vista",
            estado="CONFIRMADO", fecha_evento=date(2026, 8, 10), monto_neto=Decimal("12000"),
        )
        ConciliacionEventoExterno.objects.create(
            evento=evento, movimiento=movimiento, rol="CARGO", monto_asignado=Decimal("12000"),
            confianza=Decimal("1"), metodo="ID",
        )
        otra = CategoriaTematica.objects.create(
            user=self.other, nombre="Privada", aplica_a="EGRESO"
        )
        self.client.force_login(self.user)
        ruta = reverse("banca_reclasificar_neat", args=[evento.pk])
        self.assertEqual(self.client.post(ruta, {"categoria": otra.pk}).status_code, 404)
        response = self.client.get(reverse("gastos_movimientos"), {
            "fecha_desde": "2026-08-01", "fecha_hasta": "2026-08-31"
        })
        self.assertContains(response, "Categoría del pago Neat")
        self.assertEqual(self.client.post(ruta, {"categoria": self.transport.pk}).status_code, 302)
        evento.refresh_from_db()
        movimiento.refresh_from_db()
        self.assertEqual(evento.categoria, self.transport)
        self.assertTrue(evento.categoria_es_manual)
        self.assertEqual(movimiento.categoria, self.food)
        actualizado = self.client.get(reverse("gastos_movimientos"), {
            "fecha_desde": "2026-08-01", "fecha_hasta": "2026-08-31"
        })
        self.assertEqual(actualizado.context["categoria_chart"][0]["nombre"], "Transporte")

        self.client.force_login(self.other)
        self.assertEqual(self.client.post(ruta, {"categoria": otra.pk}).status_code, 404)

    def test_pago_de_tdc_con_otra_tdc_es_financiamiento_no_consumo(self):
        movimiento = self.movement(
            "Recaudacion Hiper Lina Linares",
            self.food,
            "pago-tdc-con-tdc",
            amount="454445",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("banca_asignar_prestamo", args=[movimiento.pk]),
            {"prestamo": "transferencia"},
        )

        self.assertEqual(response.status_code, 302)
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.naturaleza, "TRANSFERENCIA")
        self.assertEqual(movimiento.categoria.nombre, "Financiamiento / Pago de TDC")
        self.assertTrue(movimiento.naturaleza_es_manual)
        self.assertTrue(movimiento.categoria_es_manual)
        consumo = resumen_gastos(
            self.user, date(2026, 8, 1), date(2026, 8, 1), "consumo"
        )
        self.assertEqual(consumo["totales"]["egresos_clp"], Decimal("0"))

        pantalla = self.client.get(reverse("banca_movimientos"))
        self.assertContains(pantalla, "Pago de tarjeta de crédito")

    def test_abono_puede_registrarse_como_pago_de_cuenta_por_cobrar(self):
        cuenta = CuentaPorCobrar.objects.create(user=self.user, nombre="Loyo Anthony")
        from patrimonio.models import DetalleCuentaPorCobrar
        from patrimonio.services.revision_cobros import preparar_calculo, guardar_decisiones, confirmar_calculo
        DetalleCuentaPorCobrar.objects.create(cuenta=cuenta, periodo=date(2026, 8, 1), fecha=date(2026, 8, 1), descripcion="Deuda", monto=200000)
        movimiento = self.movement(
            "Transf. LOYO ANTHONY MENDOZA",
            None,
            "pago-cuenta-cobrar",
            amount="-200000",
        )
        movimiento.por_revisar = True
        movimiento.save(update_fields=["por_revisar"])
        self.client.force_login(self.user)

        pendiente = self.client.get(reverse("banca_movimientos"), {"pendientes": "1"})
        self.assertContains(pendiente, "Pago de cuenta por cobrar")
        response = self.client.post(
            reverse("banca_asignar_abono_cuenta_cobrar", args=[movimiento.pk]),
            {"cuenta_cobro": cuenta.pk,
             "volver": reverse("gastos_movimientos") + "?pendientes=1&fecha_desde=&fecha_hasta="},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("gastos_movimientos") + "?pendientes=1&fecha_desde=&fecha_hasta=")
        self.assertFalse(AbonoCuentaPorCobrar.objects.filter(movimiento=movimiento).exists())
        bandeja = self.client.get(response.url)
        self.assertContains(bandeja, "La transferencia del 10/08/2026")
        self.assertContains(bandeja, "Revisar propuesta y conciliación")
        self.assertContains(bandeja, "Asociado a Loyo Anthony · pendiente de validar")
        periodo = cuenta.partidas_revision.get().periodo
        calculo = preparar_calculo(cuenta, periodo)
        guardar_decisiones(calculo, {str(p.pk): {"decision": "APLICAR"} for p in cuenta.partidas_revision.all()})
        calculo.refresh_from_db()
        confirmar_calculo(calculo, calculo.huella)
        movimiento.refresh_from_db()
        self.assertEqual(movimiento.naturaleza, "TRANSFERENCIA")
        self.assertFalse(movimiento.por_revisar)
        abono = AbonoCuentaPorCobrar.objects.get(movimiento=movimiento)
        self.assertEqual(abono.cuenta_id, cuenta.pk)
        self.assertEqual(abono.monto, Decimal("200000"))
        consumo = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "consumo")
        self.assertEqual(consumo["totales"]["ingresos_clp"], Decimal("0"))
        caja = resumen_gastos(self.user, date(2026, 8, 1), date(2026, 8, 1), "caja")
        self.assertEqual(caja["totales"]["ingresos_clp"], Decimal("200000"))
