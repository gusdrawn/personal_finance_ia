"""Pruebas de regresión para los servicios de banca.

Estos tests fijan contratos pequeños y reutilizables para futuras modificaciones: los
movimientos deben conservar su semántica, el presupuesto debe excluir transferencias y
la clasificación IA nunca debe requerir red durante la suite.
"""

import json
from datetime import date
from decimal import Decimal
from unittest.mock import Mock, patch

from django.contrib.auth.models import User
from django.test import TestCase

from banca.models import (
    CategoriaTematica,
    ConfiguracionBanca,
    ContraparteConocida,
    ConciliacionEventoExterno,
    EventoFinancieroExterno,
    MapeoComercio,
    MovimientoBancario,
    PagoNeat,
    SaldoSnapshot,
)
from banca.services import analisis, clasificacion_ia, notificador, presupuesto, reconciliacion
from configuracion.models import Banco, Producto
from prestamos.models import Prestamo


class BancaServiceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="services", password="x")
        self.banco = Banco.objects.create(nombre="Banco Servicios")
        self.producto = Producto.objects.create(
            user=self.user, banco=self.banco, nombre="TDC Servicios", tipo="TDC"
        )

    def crear_movimiento(self, *, descripcion="Compra", monto_clp=100, **extra):
        defaults = {
            "user": self.user,
            "producto": self.producto,
            "fecha": date(2026, 6, 15),
            "descripcion": descripcion,
            "monto": Decimal(str(monto_clp)),
            "monto_clp": Decimal(str(monto_clp)),
            "hash_externo": f"hash-{MovimientoBancario.objects.count()}-{descripcion}",
        }
        defaults.update(extra)
        return MovimientoBancario.objects.create(**defaults)

    def crear_prestamo(self, *, nombre="Bicicleta", monto="1000"):
        return Prestamo.objects.create(
            user=self.user,
            nombre=nombre,
            tipo="TERCEROS",
            monto_total=Decimal(monto),
        )


class ReconciliacionServiceTest(BancaServiceTestCase):
    def test_abono_con_mapeo_de_ingreso_no_queda_por_revisar(self):
        categoria = CategoriaTematica.objects.create(
            user=self.user, nombre="Arriendos", aplica_a="INGRESO"
        )
        clasificacion_ia.guardar_mapeo_comercio(
            self.user, "INSURTECH", categoria, origen="MANUAL"
        )
        movimiento = self.crear_movimiento(
            descripcion="Transf. INSURTECH SPA", monto_clp=-318065
        )

        reconciliacion.reconciliar(self.user)
        resultado = clasificacion_ia.clasificar_pendientes(
            self.user, clasificador=Mock()
        )

        movimiento.refresh_from_db()
        self.assertFalse(movimiento.por_revisar)
        self.assertEqual(movimiento.naturaleza, "OPERACIONAL")
        self.assertEqual(movimiento.categoria, categoria)
        self.assertEqual(resultado, {"regla": 1, "ia": 0, "sin_resolver": 0})

    def test_traspasos_espejados_y_pagos_de_productos_propios_no_son_gasto(self):
        cuenta_a = Producto.objects.create(
            user=self.user, banco=self.banco, nombre="Cuenta A", tipo="CUENTA"
        )
        cuenta_b = Producto.objects.create(
            user=self.user, banco=self.banco, nombre="Cuenta B", tipo="CUENTA"
        )
        salida = self.crear_movimiento(
            producto=cuenta_a, descripcion="Transf a Ana Maria Perez", monto_clp=5000000
        )
        entrada = self.crear_movimiento(
            producto=cuenta_b, descripcion="Transf de ANA MARIA PEREZ", monto_clp=-5000000
        )
        otra_salida = self.crear_movimiento(
            producto=cuenta_a, descripcion="Transf a Ana Maria Perez", monto_clp=2000000
        )
        pago_tarjeta = self.crear_movimiento(
            producto=cuenta_a, descripcion="Pago Tarjeta De Credito", monto_clp=700000
        )

        reconciliacion.reconciliar(self.user)

        for movimiento in (salida, entrada, otra_salida, pago_tarjeta):
            movimiento.refresh_from_db()
            self.assertEqual(movimiento.naturaleza, "TRANSFERENCIA")
            self.assertFalse(movimiento.por_revisar)

    def test_abono_bicicleta_se_cruza_con_destinatario_y_montos_neat(self):
        prestamo = self.crear_prestamo(nombre="Bicicleta Jonathan", monto="5000000")
        otro_usuario = User.objects.create_user(username="neat-ajeno", password="x")
        for indice, monto in enumerate(("2022240", "3033360"), 1):
            evento = EventoFinancieroExterno.objects.create(
                user=self.user,
                fuente="neat",
                tipo="PAGO",
                external_id=f"jonathan-{indice}",
                estado="CONFIRMADO",
                fecha_evento=date(2026, 8, 2),
                monto_neto=Decimal(monto),
            )
            PagoNeat.objects.create(
                evento=evento,
                destinatario="Jonathan Jose Rivero Perez",
                monto_pagado=Decimal(monto),
                total_cargado=Decimal(monto),
            )
        evento_ajeno = EventoFinancieroExterno.objects.create(
            user=otro_usuario,
            fuente="neat",
            tipo="PAGO",
            external_id="ajeno",
            estado="CONFIRMADO",
            fecha_evento=date(2026, 8, 2),
            monto_neto=Decimal("5000000"),
        )
        PagoNeat.objects.create(
            evento=evento_ajeno,
            destinatario="Jonathan Jose Rivero Perez",
            monto_pagado=Decimal("5000000"),
            total_cargado=Decimal("5000000"),
        )
        movimiento = self.crear_movimiento(
            descripcion="TRANSF. DE JONATHAN JOSE RIVERO PEREZ",
            monto_clp=Decimal("5000000") * -1,
            fecha=date(2026, 8, 5),
        )

        reconciliacion.reconciliar(self.user)
        reconciliacion.reconciliar(self.user)

        movimiento.refresh_from_db()
        self.assertEqual(movimiento.naturaleza, "TRANSFERENCIA")
        self.assertEqual(movimiento.prestamo, prestamo)
        self.assertFalse(movimiento.por_revisar)
        self.assertEqual(
            ConciliacionEventoExterno.objects.filter(
                movimiento=movimiento, rol="DEPOSITO"
            ).count(),
            2,
        )
        self.assertFalse(
            ConciliacionEventoExterno.objects.filter(evento=evento_ajeno).exists()
        )

    def test_pago_de_tarjeta_se_marca_como_transferencia(self):
        movimiento = self.crear_movimiento(
            descripcion="PAGO TDC JUNIO", monto_clp=-100
        )

        resultado = reconciliacion.reconciliar(self.user)

        movimiento.refresh_from_db()
        self.assertEqual(resultado, {"transferencias": 1, "por_revisar": 0})
        self.assertEqual(movimiento.naturaleza, "TRANSFERENCIA")
        self.assertFalse(movimiento.por_revisar)

    def test_monto_cancelado_es_pago_tdc_pero_pago_online_positivo_no(self):
        cancelado = self.crear_movimiento(
            descripcion="MONTO CANCELADO", monto_clp=-7090094
        )
        compra = self.crear_movimiento(
            descripcion="PAGO ONLINE KUSHKI", monto_clp=8000
        )

        reconciliacion.reconciliar(self.user)

        cancelado.refresh_from_db()
        compra.refresh_from_db()
        self.assertEqual(cancelado.naturaleza, "TRANSFERENCIA")
        self.assertEqual(compra.naturaleza, "OPERACIONAL")

    def test_contraparte_conocida_vincula_el_prestamo(self):
        prestamo = self.crear_prestamo(nombre="Préstamo Juan")
        ContraparteConocida.objects.create(
            user=self.user, alias="Juan Perez", prestamo=prestamo
        )
        movimiento = self.crear_movimiento(
            descripcion="TRANSFERENCIA JUAN PEREZ", monto_clp=-100
        )

        reconciliacion.reconciliar(self.user)

        movimiento.refresh_from_db()
        self.assertEqual(movimiento.naturaleza, "TRANSFERENCIA")
        self.assertEqual(movimiento.prestamo_id, prestamo.id)

    def test_cargo_neat_cabe_en_un_prestamo_y_se_vincula(self):
        prestamo = self.crear_prestamo(monto="1000")
        movimiento = self.crear_movimiento(
            descripcion="NEAT PAGOS", monto_clp=Decimal("1020")
        )

        reconciliacion.reconciliar(self.user)

        movimiento.refresh_from_db()
        self.assertEqual(movimiento.naturaleza, "TRANSFERENCIA")
        self.assertEqual(movimiento.prestamo_id, prestamo.id)

    def test_vinculo_de_prestamo_no_se_deshace_al_reconciliar_de_nuevo(self):
        prestamo = self.crear_prestamo(monto="1000")
        movimiento = self.crear_movimiento(
            descripcion="NEAT MONTOS ALTOS",
            monto_clp=Decimal("1020"),
            prestamo=prestamo,
            naturaleza="TRANSFERENCIA",
        )
        prestamo.activo = False
        prestamo.save(update_fields=["activo"])

        reconciliacion.reconciliar(self.user)

        movimiento.refresh_from_db()
        self.assertEqual(movimiento.naturaleza, "TRANSFERENCIA")
        self.assertEqual(movimiento.prestamo, prestamo)

    def test_cargo_neat_ambiguo_queda_para_revision(self):
        self.crear_prestamo(nombre="Bicicleta A")
        self.crear_prestamo(nombre="Bicicleta B")
        movimiento = self.crear_movimiento(
            descripcion="NEAT PAGOS", monto_clp=Decimal("1020")
        )

        resultado = reconciliacion.reconciliar(self.user)

        movimiento.refresh_from_db()
        self.assertEqual(resultado["por_revisar"], 1)
        self.assertEqual(movimiento.naturaleza, "OPERACIONAL")
        self.assertTrue(movimiento.por_revisar)

    def test_abono_no_reconocido_no_se_cuenta_como_ingreso(self):
        movimiento = self.crear_movimiento(
            descripcion="TRANSFERENCIA RECIBIDA DESCONOCIDA", monto_clp=-100
        )

        reconciliacion.reconciliar(self.user)

        movimiento.refresh_from_db()
        self.assertTrue(movimiento.por_revisar)
        self.assertTrue(movimiento.contabiliza is False)

    def test_naturaleza_fijada_manualmente_no_se_pisa(self):
        movimiento = self.crear_movimiento(
            descripcion="PAGO MANUAL", monto_clp=-100, naturaleza_es_manual=True
        )

        reconciliacion.reconciliar(self.user)

        movimiento.refresh_from_db()
        self.assertEqual(movimiento.naturaleza, "OPERACIONAL")
        self.assertFalse(movimiento.por_revisar)


class PresupuestoServiceTest(BancaServiceTestCase):
    def ingreso(self, fecha, monto):
        return self.crear_movimiento(
            descripcion=f"Ingreso {fecha}",
            monto_clp=-Decimal(str(monto)),
            fecha=fecha,
        )

    def test_promedio_usa_meses_con_ingresos_y_excluye_transferencias(self):
        self.ingreso(date(2026, 4, 10), 3000)
        self.ingreso(date(2026, 5, 10), 3300)
        self.ingreso(date(2026, 6, 10), 3600)
        self.crear_movimiento(
            descripcion="Transferencia", monto_clp=-900, fecha=date(2026, 6, 11),
            naturaleza="TRANSFERENCIA"
        )

        with patch(
            "banca.services.presupuesto.timezone.localdate",
            return_value=date(2026, 6, 15),
        ):
            promedio = presupuesto.promedio_ingresos(self.user, ventana_meses=3)

        self.assertEqual(promedio, Decimal("3300"))

    def test_comprometido_incluye_fijos_y_cuotas_but_not_ocio_simple(self):
        self.crear_movimiento(
            descripcion="Arriendo", monto_clp=1000, esencialidad="FIJO"
        )
        self.crear_movimiento(
            descripcion="Cuota", monto_clp=800, esencialidad="OCIO", cuota_total=12
        )
        self.crear_movimiento(
            descripcion="Salida", monto_clp=500, esencialidad="OCIO", cuota_total=None
        )

        self.assertEqual(
            presupuesto.comprometido_mes(self.user, 2026, 6), Decimal("1800")
        )
        self.assertEqual(
            presupuesto.ocio_gastado_mes(self.user, 2026, 6), Decimal("500")
        )

    def test_resumen_dispara_alerta_con_el_umbral_configurado(self):
        for month in (4, 5, 6):
            self.ingreso(date(2026, month, 10), 3000)
        self.crear_movimiento(
            descripcion="Fijo", monto_clp=1000, esencialidad="FIJO"
        )
        self.crear_movimiento(
            descripcion="Ocio", monto_clp=500, esencialidad="OCIO"
        )
        ConfiguracionBanca.objects.create(
            user=self.user,
            ventana_meses_ingreso=3,
            umbral_alerta_ocio=Decimal("0.40"),
        )

        with patch(
            "banca.services.presupuesto.timezone.localdate",
            return_value=date(2026, 6, 15),
        ):
            resumen = presupuesto.resumen_presupuesto(
                self.user, year=2026, month=6
            )

        self.assertEqual(resumen["promedio_ingresos"], Decimal("3000"))
        self.assertEqual(resumen["disponible_ocio"], Decimal("2000"))
        self.assertEqual(resumen["ocio_gastado"], Decimal("500"))
        self.assertEqual(resumen["porcentaje_usado"], Decimal("25.0"))
        self.assertFalse(resumen["alerta"])


class ClasificacionServiceTest(BancaServiceTestCase):
    def test_sueldo_y_comision_bancaria_se_clasifican_por_regla_y_direccion(self):
        sueldo = self.crear_movimiento(
            descripcion="REMUNERACION EMPRESA", monto_clp=-4565986
        )
        comision = self.crear_movimiento(
            descripcion="COM.MANTENCION PLAN", monto_clp=27774
        )
        clasificador = Mock()

        resultado = clasificacion_ia.clasificar_pendientes(
            self.user, clasificador=clasificador
        )

        sueldo.refresh_from_db()
        comision.refresh_from_db()
        self.assertEqual(resultado, {"regla": 2, "ia": 0, "sin_resolver": 0})
        self.assertEqual(sueldo.categoria.nombre, "Sueldos")
        self.assertEqual(sueldo.categoria.aplica_a, "INGRESO")
        self.assertEqual(comision.categoria.nombre, "Intereses y comisiones")
        self.assertEqual(comision.categoria.aplica_a, "EGRESO")
        clasificador.clasificar.assert_not_called()

    def test_normalizar_glosa_elimina_folios_y_puntuacion(self):
        self.assertEqual(
            clasificacion_ia.normalizar_glosa(" SUPERMERCADO 123456 # A "),
            "supermercado a",
        )

    def test_mapeo_existente_clasifica_sin_invocar_ia(self):
        categoria = CategoriaTematica.objects.create(
            user=self.user, nombre="Supermercado", esencialidad_defecto="FIJO"
        )
        mapeo = MapeoComercio.objects.create(
            user=self.user, patron="supermercado xyz", categoria=categoria
        )
        movimiento = self.crear_movimiento(descripcion="SUPERMERCADO XYZ 1234")
        clasificador = Mock()

        resultado = clasificacion_ia.clasificar_pendientes(
            self.user, clasificador=clasificador
        )

        movimiento.refresh_from_db()
        mapeo.refresh_from_db()
        self.assertEqual(resultado, {"regla": 1, "ia": 0, "sin_resolver": 0})
        self.assertEqual(movimiento.categoria_id, categoria.id)
        self.assertEqual(movimiento.fuente_clasificacion, "REGLA")
        self.assertEqual(mapeo.hits, 1)
        clasificador.clasificar.assert_not_called()

    def test_ia_deduplica_glosas_y_aprende_mapeo(self):
        first = self.crear_movimiento(descripcion="CAFETERIA CENTRAL 1234")
        second = self.crear_movimiento(
            descripcion="CAFETERIA CENTRAL 5678"
        )

        class FakeClassifier:
            def __init__(self):
                self.calls = []

            def clasificar(self, glosas, categorias):
                self.calls.append((glosas, categorias))
                return [{"glosa": "cafeteria central", "categoria": "Ocio", "confianza": 0.91}]

        classifier = FakeClassifier()
        resultado = clasificacion_ia.clasificar_pendientes(
            self.user, clasificador=classifier
        )

        self.assertEqual(resultado, {"regla": 0, "ia": 2, "sin_resolver": 0})
        self.assertEqual(len(classifier.calls), 1)
        self.assertEqual(classifier.calls[0][0], ["cafeteria central"])
        self.assertEqual(
            MapeoComercio.objects.get(user=self.user).patron, "cafeteria central"
        )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.fuente_clasificacion, "IA")
        self.assertEqual(second.fuente_clasificacion, "IA")

    def test_ia_usa_la_configuracion_del_usuario_actual(self):
        ConfiguracionBanca.objects.create(
            user=self.user,
            ia_base_url="https://ia.test/v1",
            modelo_ia="modelo-demo",
            ia_api_key_cifrada=b"configured",
        )
        self.crear_movimiento(descripcion="COMERCIO NUEVO")
        classifier = Mock()
        classifier.clasificar.return_value = [
            {"glosa": "comercio nuevo", "categoria": "Compras", "confianza": 0.88}
        ]

        with patch.object(
            ConfiguracionBanca,
            "get_ia_api_key",
            return_value="api-key-del-usuario",
        ), patch(
            "banca.services.clasificacion_ia.OpenAICompatibleClasificador",
            return_value=classifier,
        ) as constructor:
            resultado = clasificacion_ia.clasificar_pendientes(self.user)

        self.assertEqual(resultado, {"regla": 0, "ia": 1, "sin_resolver": 0})
        constructor.assert_called_once_with(
            modelo="modelo-demo",
            api_key="api-key-del-usuario",
            base_url="https://ia.test/v1",
        )
        self.assertTrue(MapeoComercio.objects.filter(user=self.user, origen="IA").exists())

    def test_mapeo_manual_permite_fijar_comercio_y_se_reutiliza_solo_por_usuario(self):
        categoria = CategoriaTematica.objects.create(
            user=self.user, nombre="Comida", esencialidad_defecto="FIJO"
        )
        otro_usuario = User.objects.create_user(username="otro-mapeo", password="x")
        otra_categoria = CategoriaTematica.objects.create(
            user=otro_usuario, nombre="Otra categoría"
        )
        clasificacion_ia.guardar_mapeo_comercio(
            self.user, "  Restaurante 123456  ", categoria, origen="MANUAL"
        )
        clasificacion_ia.guardar_mapeo_comercio(
            otro_usuario, "restaurante", otra_categoria, origen="MANUAL"
        )
        movimiento = self.crear_movimiento(descripcion="RESTAURANTE 999999")
        clasificador = Mock()

        resultado = clasificacion_ia.clasificar_pendientes(
            self.user, ids=[movimiento.id], clasificador=clasificador
        )

        movimiento.refresh_from_db()
        mapeo = MapeoComercio.objects.get(user=self.user)
        self.assertEqual(resultado, {"regla": 1, "ia": 0, "sin_resolver": 0})
        self.assertEqual(mapeo.patron, "restaurante")
        self.assertEqual(mapeo.origen, "MANUAL")
        self.assertEqual(movimiento.categoria_id, categoria.id)
        clasificador.clasificar.assert_not_called()

    def test_clasificacion_omite_transferencias_y_categorias_manuales(self):
        manual = self.crear_movimiento(
            descripcion="Manual", categoria_es_manual=True
        )
        transfer = self.crear_movimiento(
            descripcion="Transferencia", naturaleza="TRANSFERENCIA"
        )
        classifier = Mock()

        resultado = clasificacion_ia.clasificar_pendientes(
            self.user, clasificador=classifier
        )

        self.assertEqual(resultado, {"regla": 0, "ia": 0, "sin_resolver": 0})
        classifier.clasificar.assert_not_called()
        self.assertIsNone(manual.categoria)
        self.assertIsNone(transfer.categoria)

    def test_listar_modelos_normaliza_url_y_ordena_sin_red_real(self):
        response = Mock()
        response.json.return_value = {
            "data": [{"id": "z-model"}, {"id": "a-model"}, {"id": "a-model"}]
        }
        response.raise_for_status.return_value = None
        with patch("requests.get", return_value=response) as get:
            modelos = clasificacion_ia.listar_modelos(
                "https://example.test/v1/chat/completions/", "secret"
            )

        self.assertEqual(modelos, ["a-model", "z-model"])
        get.assert_called_once_with(
            "https://example.test/v1/models",
            headers={"Authorization": "Bearer secret"},
            timeout=30,
        )

    def test_error_del_proveedor_se_devuelve_con_su_motivo(self):
        import requests

        movimiento = self.crear_movimiento(descripcion="COMERCIO SIN CLASIFICAR")
        response = Mock(status_code=403)
        response.json.return_value = {
            "error": {
                "type": "DataPolicyError",
                "message": "Este modelo requiere consentimiento explícito.",
            }
        }
        response.raise_for_status.side_effect = requests.HTTPError(
            "403 Forbidden", response=response
        )
        clasificador = clasificacion_ia.OpenAICompatibleClasificador(
            modelo="modelo-restringido",
            api_key="secret",
            base_url="https://ia.test/v1",
        )

        with patch("requests.post", return_value=response):
            resultado = clasificacion_ia.clasificar_pendientes(
                self.user, ids=[movimiento.pk], clasificador=clasificador
            )

        self.assertEqual(resultado["ia"], 0)
        self.assertEqual(resultado["sin_resolver"], 1)
        self.assertIn("consentimiento explícito", resultado["error"])

    def test_clasificador_divide_lotes_grandes_sin_perder_glosas(self):
        glosas = [f"comercio {indice}" for indice in range(41)]

        def responder(*args, **kwargs):
            payload_usuario = json.loads(kwargs["json"]["messages"][1]["content"])
            filas = [
                {"glosa": glosa, "categoria": "Compras", "confianza": 0.9}
                for glosa in payload_usuario["glosas"]
            ]
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {
                "choices": [
                    {"message": {"content": json.dumps({"respuesta": filas})}}
                ]
            }
            return response

        clasificador = clasificacion_ia.OpenAICompatibleClasificador(
            modelo="modelo-lotes", api_key="secret", base_url="https://ia.test/v1"
        )
        with patch("requests.post", side_effect=responder) as post:
            resultado = clasificador.clasificar(glosas, ["Compras"])

        self.assertEqual(post.call_count, 3)
        self.assertEqual([fila["glosa"] for fila in resultado], glosas)
        self.assertEqual(clasificador.ultimo_error, "")

    def test_clasificador_reintenta_solo_glosas_omitidas(self):
        llamadas = []

        def responder(*args, **kwargs):
            payload_usuario = json.loads(kwargs["json"]["messages"][1]["content"])
            lote = payload_usuario["glosas"]
            llamadas.append(lote)
            devueltas = lote[:-1] if len(llamadas) == 1 else lote
            filas = [
                {"glosa": glosa, "categoria": "Compras", "confianza": 0.9}
                for glosa in devueltas
            ]
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {
                "choices": [{"message": {"content": json.dumps({"respuesta": filas})}}]
            }
            return response

        clasificador = clasificacion_ia.OpenAICompatibleClasificador(
            modelo="modelo-reintentos", api_key="secret", base_url="https://ia.test/v1"
        )
        with patch("requests.post", side_effect=responder):
            resultado = clasificador.clasificar(
                ["comercio uno", "comercio dos", "comercio tres"], ["Compras"]
            )

        self.assertEqual(len(resultado), 3)
        self.assertEqual(llamadas, [
            ["comercio uno", "comercio dos", "comercio tres"],
            ["comercio tres"],
        ])
        self.assertEqual(clasificador.ultimo_error, "")


class AnalisisServiceTest(BancaServiceTestCase):
    def test_fijo_vs_ocio_excluye_transferencias_y_abonos(self):
        self.crear_movimiento(descripcion="Fijo", monto_clp=100, esencialidad="FIJO")
        self.crear_movimiento(descripcion="Ocio", monto_clp=50, esencialidad="OCIO")
        self.crear_movimiento(
            descripcion="Transferencia", monto_clp=300, naturaleza="TRANSFERENCIA"
        )
        self.crear_movimiento(descripcion="Ingreso", monto_clp=-1000)

        self.assertEqual(
            analisis.fijo_vs_ocio(self.user, 2026, 6),
            {"fijo": Decimal("100"), "ocio": Decimal("50"), "total": Decimal("150")},
        )

    def test_por_categoria_agrupa_y_ordena_por_monto(self):
        mayor = CategoriaTematica.objects.create(
            user=self.user, nombre="Mayor", esencialidad_defecto="FIJO"
        )
        menor = CategoriaTematica.objects.create(
            user=self.user, nombre="Menor", esencialidad_defecto="OCIO"
        )
        self.crear_movimiento(descripcion="M1", monto_clp=200, categoria=mayor)
        self.crear_movimiento(descripcion="M2", monto_clp=50, categoria=mayor)
        self.crear_movimiento(descripcion="m3", monto_clp=100, categoria=menor)

        resultado = analisis.por_categoria(self.user, 2026, 6)

        self.assertEqual([r["categoria"] for r in resultado], ["Mayor", "Menor"])
        self.assertEqual(resultado[0]["total"], Decimal("250"))
        self.assertEqual(resultado[0]["n"], 2)

    def test_detectar_suscripciones_requiere_meses_distintos(self):
        for month in (4, 5):
            self.crear_movimiento(
                descripcion="NETFLIX 1234", monto_clp=1000,
                fecha=date(2026, month, 10)
            )
        self.crear_movimiento(
            descripcion="COMPRA UNICA", monto_clp=500, fecha=date(2026, 5, 11)
        )

        resultado = analisis.detectar_suscripciones(self.user)

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["comercio"], "netflix 1234")
        self.assertEqual(resultado[0]["meses"], 2)
        self.assertEqual(resultado[0]["monto_promedio"], Decimal("1000"))

    def test_monto_a_pagar_tdc_usa_el_snapshot_mas_reciente(self):
        SaldoSnapshot.objects.create(
            user=self.user,
            producto=self.producto,
            fecha=date(2026, 5, 1),
            deuda_facturada_clp=Decimal("100"),
        )
        SaldoSnapshot.objects.create(
            user=self.user,
            producto=self.producto,
            fecha=date(2026, 6, 1),
            deuda_facturada_clp=Decimal("250"),
            deuda_no_facturada_clp=Decimal("50"),
            deuda_usd=Decimal("20.50"),
        )

        resultado = analisis.monto_a_pagar_tdc(self.user)

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["facturado_clp"], Decimal("250"))
        self.assertEqual(resultado[0]["no_facturado_clp"], Decimal("50"))
        self.assertEqual(resultado[0]["facturado_usd"], Decimal("20.50"))
        self.assertEqual(resultado[0]["fecha"], date(2026, 6, 1))

    def test_cupos_tdc_usa_el_ultimo_snapshot_y_aisla_el_usuario(self):
        SaldoSnapshot.objects.create(
            user=self.user,
            producto=self.producto,
            fecha=date(2026, 5, 1),
            cupo_total_clp=Decimal("1000000"),
            cupo_disponible_clp=Decimal("200000"),
        )
        SaldoSnapshot.objects.create(
            user=self.user,
            producto=self.producto,
            fecha=date(2026, 6, 1),
            cupo_total_clp=Decimal("1500000"),
            cupo_disponible_clp=Decimal("900000"),
        )
        otro_usuario = User.objects.create_user(username="otro-servicio", password="x")
        SaldoSnapshot.objects.create(
            user=otro_usuario,
            producto=self.producto,
            fecha=date(2026, 6, 1),
            cupo_total_clp=Decimal("9000000"),
            cupo_disponible_clp=Decimal("100000"),
        )

        resultado = analisis.cupos_tdc(self.user)

        self.assertEqual(len(resultado["tarjetas"]), 1)
        self.assertEqual(resultado["total_clp"], Decimal("1500000"))
        self.assertEqual(resultado["disponible_clp"], Decimal("900000"))
        self.assertEqual(resultado["utilizado_clp"], Decimal("600000"))
        self.assertEqual(resultado["fecha"], date(2026, 6, 1))

    def test_vencimientos_tdc_filtra_mes_y_aisla_usuario(self):
        snapshot = SaldoSnapshot.objects.create(
            user=self.user,
            producto=self.producto,
            fecha=date(2026, 7, 27),
            fecha_vencimiento=date(2026, 8, 10),
            deuda_facturada_clp=Decimal("300000"),
        )
        otro_usuario = User.objects.create_user(username="otro-vencimiento", password="x")
        SaldoSnapshot.objects.create(
            user=otro_usuario,
            producto=self.producto,
            fecha=date(2026, 7, 27),
            fecha_vencimiento=date(2026, 8, 10),
            deuda_facturada_clp=Decimal("900000"),
        )

        resultado = analisis.vencimientos_tdc(self.user, 2026, 8)

        self.assertEqual(len(resultado), 1)
        self.assertEqual(resultado[0]["fecha"], snapshot.fecha_vencimiento)
        self.assertEqual(resultado[0]["monto"], Decimal("300000"))


class NotificadorServiceTest(BancaServiceTestCase):
    def test_avisar_nuevos_marca_movimientos_como_notificados(self):
        movimiento = self.crear_movimiento(
            descripcion="Compra notificable", monto_clp=1234
        )
        with patch(
            "banca.services.notificador.enviar_telegram", return_value=True
        ) as enviar:
            resultado = notificador.avisar_nuevos_no_facturados(
                self.user, [movimiento.id]
            )

        movimiento.refresh_from_db()
        self.assertTrue(resultado)
        self.assertTrue(movimiento.notificado_telegram)
        self.assertIn("Compra notificable", enviar.call_args.args[0])

    def test_avisar_nuevos_sin_movimientos_no_envia(self):
        with patch("banca.services.notificador.enviar_telegram") as enviar:
            resultado = notificador.avisar_nuevos_no_facturados(self.user, [])

        self.assertFalse(resultado)
        enviar.assert_not_called()
