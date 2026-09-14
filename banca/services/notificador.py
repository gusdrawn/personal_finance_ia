"""Alertas Telegram configurables e idempotentes por usuario."""

from __future__ import annotations

import html
import logging
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from banca.models import (
    ConfiguracionNotificaciones,
    LimiteCategoria,
    MovimientoBancario,
    RegistroNotificacion,
)

logger = logging.getLogger("banca.notificador")


def _miles(n: Decimal | int) -> str:
    try:
        return f"${int(n):,}".replace(",", ".")
    except Exception:
        return f"${n}"


def _config(user):
    if not user:
        return None
    config, _ = ConfiguracionNotificaciones.objects.get_or_create(user=user)
    return config


def enviar_telegram(
    texto: str,
    *,
    user=None,
    clave_idempotencia: str | None = None,
    tipo: str = "GENERICA",
    movimiento=None,
    permitir_inactivo: bool = False,
) -> bool:
    cfg = _config(user)
    if not cfg or (not cfg.telegram_activo and not permitir_inactivo):
        return False
    try:
        token = cfg.get_telegram_bot_token()
        chat = cfg.get_telegram_chat_id()
    except Exception as exc:
        logger.warning("No se pudo descifrar Telegram para %s: %s", user, exc)
        return False
    if not token or not chat:
        return False

    registro = None
    if clave_idempotencia:
        registro, _ = RegistroNotificacion.objects.get_or_create(
            user=user,
            canal="TELEGRAM",
            clave_idempotencia=clave_idempotencia,
            defaults={"tipo": tipo, "movimiento": movimiento, "contenido": texto},
        )
        if registro.estado == "ENVIADO":
            return True
        registro.tipo = tipo
        registro.movimiento = movimiento
        registro.contenido = texto
        registro.intentos += 1
        registro.estado = "PENDIENTE"
        registro.error = ""
        registro.save()

    try:
        import requests

        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": texto, "parse_mode": "HTML"},
            timeout=20,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning("Falló envío Telegram: %s", exc)
        if registro:
            registro.estado = "ERROR"
            registro.error = str(exc)[:1000]
            registro.save(update_fields=["estado", "error", "updated_at"])
        return False

    if registro:
        registro.estado = "ENVIADO"
        registro.error = ""
        registro.enviado_at = timezone.now()
        registro.save(update_fields=["estado", "error", "enviado_at", "updated_at"])
    return True


def probar_telegram(user) -> bool:
    ok = enviar_telegram(
        "✅ <b>FinanzasPersonalesIA conectado</b>\nLas alertas personales ya pueden llegar a este chat.",
        user=user,
        permitir_inactivo=True,
    )
    cfg = ConfiguracionNotificaciones.objects.filter(user=user).first()
    if cfg:
        cfg.ultima_prueba_at = timezone.now()
        cfg.ultima_prueba_exitosa = ok
        cfg.save(update_fields=["ultima_prueba_at", "ultima_prueba_exitosa", "updated_at"])
    return ok


def _texto_movimiento(mov: MovimientoBancario) -> str:
    categoria = mov.categoria.nombre if mov.categoria_id else "Sin categoría"
    estado = "provisional" if mov.estado == "PROVISIONAL" else "facturado"
    return (
        f"🧾 <b>Nuevo gasto {estado}</b>\n"
        f"{mov.fecha:%d/%m/%Y} · {html.escape(mov.descripcion[:80])}\n"
        f"Monto: <b>{_miles(mov.monto_clp)}</b>\n"
        f"Categoría: {html.escape(categoria)}\n"
        f"Tarjeta: {html.escape(str(mov.producto))}"
    )


def avisar_nuevos_no_facturados(user, ids: list[int]) -> bool:
    cfg = _config(user)
    if not cfg or not cfg.avisar_gasto_inmediato:
        return False
    movimientos = (
        MovimientoBancario.objects.filter(
            id__in=ids, user=user, naturaleza="OPERACIONAL", monto_clp__gt=0
        )
        .exclude(estado="DESCARTADO_CIERRE")
        .select_related("categoria", "producto", "producto__banco")
        .order_by("fecha", "id")
    )
    enviados = []
    for mov in movimientos:
        if mov.monto_clp < cfg.monto_minimo_gasto:
            continue
        if enviar_telegram(
            _texto_movimiento(mov),
            user=user,
            tipo="GASTO",
            clave_idempotencia=f"gasto:{mov.pk}",
            movimiento=mov,
        ):
            enviados.append(mov.pk)
    if enviados:
        MovimientoBancario.objects.filter(pk__in=enviados).update(notificado_telegram=True)
    avisar_limites_categoria(user)
    avisar_presupuesto(user)
    return bool(enviados)


def avisar_presupuesto(user, *, forzar: bool = False) -> bool:
    from banca.services.presupuesto import resumen_presupuesto

    cfg = _config(user)
    if not forzar and (not cfg or not cfg.avisar_ocio):
        return False
    resumen = resumen_presupuesto(user)
    if not resumen["alerta"] and not forzar:
        return False
    emoji = "🔴" if resumen["alerta"] else "🟢"
    texto = (
        f"{emoji} <b>Presupuesto Ocio {resumen['month']:02d}/{resumen['year']}</b>\n"
        f"Disponible: {_miles(resumen['disponible_ocio'])}\n"
        f"Gastado: {_miles(resumen['ocio_gastado'])} ({resumen['porcentaje_usado']}%)\n"
        f"Restante: {_miles(resumen['ocio_restante'])}"
    )
    return enviar_telegram(
        texto,
        user=user,
        tipo="UMBRAL_OCIO",
        clave_idempotencia=f"ocio:{resumen['year']}-{resumen['month']:02d}:{resumen['umbral']}",
    )


def avisar_limites_categoria(user, *, hoy=None) -> bool:
    cfg = _config(user)
    if not cfg or not cfg.avisar_limites_categoria:
        return False
    hoy = hoy or timezone.localdate()
    enviado = False
    limites = LimiteCategoria.objects.filter(user=user, activo=True).select_related("categoria")
    for limite in limites:
        total = (
            MovimientoBancario.objects.filter(
                user=user,
                categoria=limite.categoria,
                naturaleza="OPERACIONAL",
                por_revisar=False,
                monto_clp__gt=0,
                fecha__year=hoy.year,
                fecha__month=hoy.month,
            )
            .exclude(estado="DESCARTADO_CIERRE")
            .aggregate(total=Sum("monto_clp"))["total"]
            or Decimal("0")
        )
        if total < limite.monto_mensual_clp:
            continue
        texto = (
            f"⚠️ <b>Límite mensual superado</b>\n"
            f"{html.escape(limite.categoria.nombre)}: {_miles(total)}\n"
            f"Límite configurado: {_miles(limite.monto_mensual_clp)}"
        )
        enviado = enviar_telegram(
            texto,
            user=user,
            tipo="LIMITE_CATEGORIA",
            clave_idempotencia=f"categoria:{limite.pk}:{hoy.year}-{hoy.month:02d}",
        ) or enviado
    return enviado


def avisar_resumen_importacion(user, importacion) -> bool:
    cfg = _config(user)
    if not cfg or not cfg.resumen_importacion_pdf:
        return False
    texto = (
        f"📄 <b>Estado de cuenta analizado</b>\n"
        f"{html.escape(importacion.nombre_archivo)} · {html.escape(importacion.banco_detectado)}\n"
        f"Movimientos nuevos: {importacion.operaciones_nuevas}\n"
        f"Apple Pay confirmados: {importacion.provisionales_confirmados} · "
        f"no encontrados: {importacion.provisionales_descartados} · "
        f"ambiguos: {importacion.provisionales_ambiguos}"
    )
    return enviar_telegram(
        texto,
        user=user,
        tipo="IMPORTACION_PDF",
        clave_idempotencia=f"importacion:{importacion.pk}",
    )


def avisar_provisionales_atrasados(user, *, hoy=None) -> bool:
    cfg = _config(user)
    if not cfg or not cfg.avisar_provisional_atrasado:
        return False
    hoy = hoy or timezone.localdate()
    movimientos = MovimientoBancario.objects.filter(
        user=user,
        origen="APPLE_PAY",
        estado="PROVISIONAL",
        provisional_hasta__lt=hoy,
    ).select_related("producto")
    enviado = False
    for mov in movimientos:
        texto = (
            f"⏳ <b>Falta estado de cuenta</b>\n"
            f"La compra {html.escape(mov.descripcion[:60])} por {_miles(mov.monto_clp)} "
            f"sigue provisional desde {mov.fecha:%d/%m/%Y}."
        )
        enviado = enviar_telegram(
            texto,
            user=user,
            tipo="APPLE_PAY_ATRASADO",
            clave_idempotencia=f"apple-atrasado:{mov.pk}",
            movimiento=mov,
        ) or enviado
    return enviado


def enviar_resumen_diario(user, *, hoy=None) -> bool:
    cfg = _config(user)
    if not cfg or not cfg.resumen_diario:
        return False
    hoy = hoy or timezone.localdate()
    movimientos = list(
        MovimientoBancario.objects.filter(
            user=user, fecha=hoy, naturaleza="OPERACIONAL", monto_clp__gt=0
        )
        .exclude(estado="DESCARTADO_CIERRE")
        .select_related("categoria")
    )
    if not movimientos:
        return False
    total = sum((mov.monto_clp for mov in movimientos), Decimal("0"))
    lineas = [
        f"• {html.escape(mov.descripcion[:35])} {_miles(mov.monto_clp)}"
        for mov in movimientos[:20]
    ]
    texto = f"📊 <b>Resumen diario {hoy:%d/%m}</b> · {_miles(total)}\n" + "\n".join(lineas)
    return enviar_telegram(
        texto,
        user=user,
        tipo="RESUMEN_DIARIO",
        clave_idempotencia=f"resumen:{hoy.isoformat()}",
    )
