import os
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from banca.models import (
    CategoriaTematica,
    ConfiguracionNotificaciones,
    ImportacionEstadoCuenta,
    LimiteCategoria,
    MapeoComercio,
    MovimientoBancario,
    RegistroNotificacion,
)
from banca.services.clasificacion_ia import recalcular_clasificaciones
from banca.services.notificador import (
    avisar_limites_categoria,
    avisar_nuevos_no_facturados,
    avisar_presupuesto,
    avisar_provisionales_atrasados,
    enviar_telegram,
    enviar_resumen_diario,
    probar_telegram,
)
from banca.services.provisionales import cerrar_periodo_apple_pay
from configuracion.models import Banco, Producto
from core.services.recordatorios import enviar_recordatorios


class IntegridadProvisionalesTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="integridad", password="x")
        self.other = User.objects.create_user(username="integridad_otro", password="x")
        self.banco = Banco.objects.create(nombre="Banco Integridad")
        self.producto = Producto.objects.create(
            user=self.user, banco=self.banco, nombre="TDC Integridad *1234", tipo="TDC"
        )

    def movimiento(self, *, user=None, descripcion, fecha, monto="12990", **extra):
        user = user or self.user
        return MovimientoBancario.objects.create(
            user=user,
            producto=self.producto,
            fecha=fecha,
            descripcion=descripcion,
            monto=Decimal(monto),
            monto_clp=Decimal(monto),
            hash_externo=extra.pop("hash_externo", f"{user.pk}-{descripcion}-{fecha}-{MovimientoBancario.objects.count()}"),
            **extra,
        )

    def importacion(self, suffix="1"):
        return ImportacionEstadoCuenta.objects.create(
            user=self.user,
            banco=self.banco,
            producto=self.producto,
            nombre_archivo=f"estado-{suffix}.pdf",
            hash_archivo=(suffix * 64)[:64],
            estado="PROCESADO",
            periodo_inicio=date(2026, 8, 1),
            periodo_fin=date(2026, 8, 31),
        )

    def test_pdf_confirma_la_misma_fila_y_no_duplica_el_gasto(self):
        categoria = CategoriaTematica.objects.create(user=self.user, nombre="Comida")
        provisional = self.movimiento(
            descripcion="CAFETERIA CENTRO",
            fecha=date(2026, 8, 10),
            origen="APPLE_PAY",
            estado="PROVISIONAL",
            categoria=categoria,
            categoria_es_manual=True,
            fuente_clasificacion="MANUAL",
            notificado_telegram=True,
        )
        original_id = provisional.pk
        pdf = self.movimiento(
            descripcion="CAFETERIA CENTRO SANTIAGO",
            fecha=date(2026, 8, 11),
            origen="PDF",
            estado="FACTURADO",
            hash_externo="hash-bancario-definitivo",
        )
        importacion = self.importacion()
        importacion.movimientos.add(pdf)

        resultado = cerrar_periodo_apple_pay(
            self.user,
            self.producto,
            date(2026, 8, 1),
            date(2026, 8, 31),
            [pdf],
            importacion,
        )

        confirmado = MovimientoBancario.objects.get(pk=original_id)
        self.assertEqual(resultado["confirmados"], 1)
        self.assertEqual(MovimientoBancario.objects.filter(user=self.user).count(), 1)
        self.assertEqual(confirmado.estado, "FACTURADO")
        self.assertEqual(confirmado.origen, "PDF")
        self.assertEqual(confirmado.hash_externo, "hash-bancario-definitivo")
        self.assertEqual(confirmado.categoria, categoria)
        self.assertTrue(confirmado.notificado_telegram)
        self.assertTrue(importacion.movimientos.filter(pk=original_id).exists())

    def test_no_aparece_se_descarta_y_ambiguedad_no_adivina(self):
        no_aparece = self.movimiento(
            descripcion="COMPRA ANULADA",
            fecha=date(2026, 8, 5),
            origen="APPLE_PAY",
            estado="PROVISIONAL",
        )
        ambiguo = self.movimiento(
            descripcion="COMERCIO",
            fecha=date(2026, 8, 12),
            origen="APPLE_PAY",
            estado="PROVISIONAL",
            monto="5000",
        )
        candidatos = [
            self.movimiento(
                descripcion=f"COMERCIO SUCURSAL {n}",
                fecha=date(2026, 8, 12),
                monto="5000",
                origen="PDF",
                estado="FACTURADO",
            )
            for n in (1, 2)
        ]
        importacion = self.importacion("2")

        resultado = cerrar_periodo_apple_pay(
            self.user, self.producto, date(2026, 8, 1), date(2026, 8, 31), candidatos, importacion
        )

        no_aparece.refresh_from_db()
        ambiguo.refresh_from_db()
        self.assertEqual(resultado, {"confirmados": 0, "descartados": 1, "ambiguos": 1, "sin_periodo": False})
        self.assertEqual(no_aparece.motivo_cierre, "NO_APARECE_EN_PDF")
        self.assertEqual(ambiguo.motivo_cierre, "AMBIGUO_PDF")
        self.assertFalse(no_aparece.contabiliza)

    def test_sin_periodo_no_cierra_y_otro_usuario_queda_aislado(self):
        propio = self.movimiento(
            descripcion="PROPIO", fecha=date(2026, 8, 5), origen="APPLE_PAY", estado="PROVISIONAL"
        )
        ajeno = self.movimiento(
            user=self.other,
            descripcion="AJENO",
            fecha=date(2026, 8, 5),
            origen="APPLE_PAY",
            estado="PROVISIONAL",
        )
        resultado = cerrar_periodo_apple_pay(
            self.user, self.producto, None, None, [], self.importacion("3")
        )
        propio.refresh_from_db()
        ajeno.refresh_from_db()
        self.assertTrue(resultado["sin_periodo"])
        self.assertEqual(propio.estado, "PROVISIONAL")
        self.assertEqual(ajeno.estado, "PROVISIONAL")


class RecalculoClasificacionTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="recalculo", password="x")
        banco = Banco.objects.create(nombre="Banco Recalculo")
        self.producto = Producto.objects.create(user=self.user, banco=banco, nombre="TDC", tipo="TDC")
        self.antigua = CategoriaTematica.objects.create(user=self.user, nombre="Antigua")
        self.manual = CategoriaTematica.objects.create(user=self.user, nombre="Manual")

    def movimiento(self, descripcion, **extra):
        return MovimientoBancario.objects.create(
            user=self.user,
            producto=self.producto,
            fecha=date(2026, 8, 1),
            descripcion=descripcion,
            monto=Decimal("1000"),
            monto_clp=Decimal("1000"),
            hash_externo=descripcion,
            **extra,
        )

    def test_ignora_aprendizaje_ia_antiguo_y_protege_manual(self):
        automatico = self.movimiento(
            "TIENDA CENTRAL", categoria=self.antigua, fuente_clasificacion="IA"
        )
        fijado = self.movimiento(
            "DECISION USUARIO",
            categoria=self.manual,
            fuente_clasificacion="MANUAL",
            categoria_es_manual=True,
        )
        pendiente = self.movimiento("COMERCIO NUEVO")
        MapeoComercio.objects.create(
            user=self.user, patron="tienda central", categoria=self.antigua, origen="IA"
        )

        class Clasificador:
            def clasificar(self, glosas, categorias):
                return [
                    {"glosa": glosa, "categoria": "Actualizada", "confianza": 0.95}
                    for glosa in glosas
                ]

        resultado = recalcular_clasificaciones(self.user, clasificador=Clasificador())

        automatico.refresh_from_db()
        fijado.refresh_from_db()
        pendiente.refresh_from_db()
        self.assertEqual(resultado["recalculados"], 2)
        self.assertEqual(resultado["manuales_protegidos"], 1)
        self.assertEqual(automatico.categoria.nombre, "Actualizada")
        self.assertEqual(pendiente.categoria.nombre, "Actualizada")
        self.assertEqual(fijado.categoria, self.manual)

    def test_fallo_del_proveedor_revierte_el_recalculo_completo(self):
        automatico = self.movimiento(
            "CATEGORIA QUE DEBE SOBREVIVIR",
            categoria=self.antigua,
            fuente_clasificacion="IA",
        )

        class ClasificadorFallido:
            ultimo_error = "Proveedor IA (403): requiere consentimiento"

            def clasificar(self, glosas, categorias):
                return []

        resultado = recalcular_clasificaciones(
            self.user, clasificador=ClasificadorFallido()
        )

        automatico.refresh_from_db()
        self.assertEqual(automatico.categoria, self.antigua)
        self.assertEqual(automatico.fuente_clasificacion, "IA")
        self.assertEqual(resultado["recalculados"], 0)
        self.assertEqual(resultado["restaurados"], 1)
        self.assertIn("consentimiento", resultado["error"])


class TelegramPersonalTest(TestCase):
    FERNET_KEY = Fernet.generate_key().decode()

    def setUp(self):
        self.env = patch.dict(os.environ, {"BANCA_FERNET_KEY": self.FERNET_KEY}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.user = User.objects.create_user(username="telegram", password="x")
        self.other = User.objects.create_user(username="telegram_otro", password="x")
        banco = Banco.objects.create(nombre="Banco Telegram")
        self.producto = Producto.objects.create(user=self.user, banco=banco, nombre="TDC Telegram", tipo="TDC")

    def config(self, user=None, token="123456:secreto", chat="111"):
        cfg = ConfiguracionNotificaciones.objects.create(user=user or self.user, telegram_activo=True)
        cfg.set_telegram(token, chat)
        cfg.save()
        return cfg

    def movimiento(self, **extra):
        defaults = {
            "user": self.user,
            "producto": self.producto,
            "fecha": date(2026, 8, 31),
            "descripcion": "Compra Telegram",
            "monto": Decimal("20000"),
            "monto_clp": Decimal("20000"),
            "hash_externo": f"telegram-{MovimientoBancario.objects.count()}",
        }
        defaults.update(extra)
        return MovimientoBancario.objects.create(**defaults)

    def test_credenciales_cifradas_y_chats_aislados(self):
        cfg = self.config()
        other_cfg = self.config(self.other, token="999999:otro", chat="222")
        self.assertNotIn(b"secreto", bytes(cfg.telegram_bot_token_cifrado))
        self.assertNotIn(b"111", bytes(cfg.telegram_chat_id_cifrado))
        self.assertEqual(cfg.get_telegram_chat_id(), "111")
        self.assertEqual(other_cfg.get_telegram_chat_id(), "222")

        response = Mock()
        response.raise_for_status.return_value = None
        with patch("requests.post", return_value=response) as post:
            enviar_telegram("uno", user=self.user, clave_idempotencia="uno")
            enviar_telegram("dos", user=self.other, clave_idempotencia="dos")
        self.assertEqual([call.kwargs["json"]["chat_id"] for call in post.call_args_list], ["111", "222"])

        self.client.force_login(self.user)
        pagina = self.client.get(reverse("configuracion"))
        self.assertNotContains(pagina, "123456:secreto")

    def test_prueba_de_conexion_guarda_resultado_comprensible(self):
        cfg = self.config()
        response = Mock()
        response.raise_for_status.return_value = None
        with patch("requests.post", return_value=response):
            self.assertTrue(probar_telegram(self.user))
        cfg.refresh_from_db()
        self.assertTrue(cfg.ultima_prueba_exitosa)
        self.assertIsNotNone(cfg.ultima_prueba_at)

    def test_deduplica_gasto_y_error_no_afecta_movimiento(self):
        self.config()
        movimiento = self.movimiento(origen="APPLE_PAY", estado="PROVISIONAL")
        response = Mock()
        response.raise_for_status.return_value = None
        with patch("requests.post", return_value=response) as post:
            self.assertTrue(avisar_nuevos_no_facturados(self.user, [movimiento.pk]))
            self.assertTrue(avisar_nuevos_no_facturados(self.user, [movimiento.pk]))
        self.assertEqual(post.call_count, 1)
        self.assertEqual(RegistroNotificacion.objects.filter(user=self.user, tipo="GASTO").count(), 1)

        with patch("requests.post", side_effect=RuntimeError("sin red")):
            self.assertFalse(enviar_telegram("fallo", user=self.user, clave_idempotencia="fallo"))
        self.assertTrue(MovimientoBancario.objects.filter(pk=movimiento.pk).exists())
        self.assertEqual(RegistroNotificacion.objects.get(clave_idempotencia="fallo").estado, "ERROR")

    def test_monto_minimo_limite_y_provisional_atrasado_se_envian_una_vez(self):
        cfg = self.config()
        cfg.monto_minimo_gasto = Decimal("30000")
        cfg.avisar_ocio = False
        cfg.save()
        bajo = self.movimiento(monto=Decimal("20000"), monto_clp=Decimal("20000"))
        categoria = CategoriaTematica.objects.create(user=self.user, nombre="Ocio especial")
        alto = self.movimiento(
            descripcion="Compra alta",
            monto=Decimal("40000"),
            monto_clp=Decimal("40000"),
            categoria=categoria,
        )
        atrasado = self.movimiento(
            descripcion="Compra pendiente",
            origen="APPLE_PAY",
            estado="PROVISIONAL",
            provisional_hasta=date.today() - timedelta(days=1),
        )
        LimiteCategoria.objects.create(
            user=self.user, categoria=categoria, monto_mensual_clp=Decimal("35000")
        )
        response = Mock()
        response.raise_for_status.return_value = None
        with patch("requests.post", return_value=response) as post:
            avisar_nuevos_no_facturados(self.user, [bajo.pk, alto.pk])
            avisar_limites_categoria(self.user, hoy=date(2026, 8, 31))
            avisar_limites_categoria(self.user, hoy=date(2026, 8, 31))
            avisar_provisionales_atrasados(self.user, hoy=date.today())
            avisar_provisionales_atrasados(self.user, hoy=date.today())
        tipos = list(RegistroNotificacion.objects.values_list("tipo", flat=True))
        self.assertNotIn(f"gasto:{bajo.pk}", RegistroNotificacion.objects.values_list("clave_idempotencia", flat=True))
        self.assertEqual(tipos.count("LIMITE_CATEGORIA"), 1)
        self.assertEqual(tipos.count("APPLE_PAY_ATRASADO"), 1)
        self.assertEqual(post.call_count, 3)

    def test_mismo_vencimiento_no_se_repite_en_ejecuciones_diarias(self):
        self.config()
        items = [{
            "fecha": date(2026, 9, 2),
            "nombre": "Pago TDC",
            "monto": Decimal("150000"),
            "tipo": "programado",
        }]
        response = Mock()
        response.raise_for_status.return_value = None
        with patch("core.services.recordatorios.vencimientos_proximos", return_value=items), patch(
            "requests.post", return_value=response
        ) as post:
            self.assertTrue(enviar_recordatorios(self.user, dias=3, hoy=date(2026, 8, 31)))
            self.assertTrue(enviar_recordatorios(self.user, dias=3, hoy=date(2026, 9, 1)))
        self.assertEqual(post.call_count, 1)
        self.assertEqual(RegistroNotificacion.objects.filter(tipo="VENCIMIENTO").count(), 1)

    def test_resumen_diario_es_configurable_e_idempotente(self):
        cfg = self.config()
        cfg.resumen_diario = True
        cfg.save()
        self.movimiento(fecha=date(2026, 8, 31))
        response = Mock()
        response.raise_for_status.return_value = None
        with patch("requests.post", return_value=response) as post:
            self.assertTrue(enviar_resumen_diario(self.user, hoy=date(2026, 8, 31)))
            self.assertTrue(enviar_resumen_diario(self.user, hoy=date(2026, 8, 31)))
        self.assertEqual(post.call_count, 1)
        self.assertEqual(RegistroNotificacion.objects.filter(tipo="RESUMEN_DIARIO").count(), 1)

    def test_umbral_ocio_se_notifica_una_vez_por_mes(self):
        self.config()
        resumen = {
            "alerta": True,
            "month": 8,
            "year": 2026,
            "disponible_ocio": Decimal("100000"),
            "ocio_gastado": Decimal("85000"),
            "porcentaje_usado": Decimal("85"),
            "ocio_restante": Decimal("15000"),
            "umbral": Decimal("0.80"),
        }
        response = Mock()
        response.raise_for_status.return_value = None
        with patch("banca.services.presupuesto.resumen_presupuesto", return_value=resumen), patch(
            "requests.post", return_value=response
        ) as post:
            self.assertTrue(avisar_presupuesto(self.user))
            self.assertTrue(avisar_presupuesto(self.user))
        self.assertEqual(post.call_count, 1)
        self.assertEqual(RegistroNotificacion.objects.filter(tipo="UMBRAL_OCIO").count(), 1)
