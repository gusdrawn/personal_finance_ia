"""POC de recepción de pagos desde un Atajo de Apple Pay.

El servicio mantiene el endpoint deliberadamente pequeño: recibe un evento normalizado,
lo convierte en `MovimientoBancario`, reutiliza las reglas/IA de banca y dispara el
notificador existente. La llave del iPhone se valida contra un hash y cada `event_id` es
único por usuario para hacer seguros los reintentos de Shortcuts.
"""

from __future__ import annotations

import hashlib
import json
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from banca.models import ApplePayApiKey, ApplePayEvent, MovimientoBancario

logger = logging.getLogger("banca.apple_pay")

MAX_AMOUNT = Decimal("9999999999999.99")
SUPPORTED_CURRENCIES = {"CLP", "USD"}


class ApplePayPayloadError(ValueError):
    """Payload inválido enviado por el Atajo."""

    def __init__(self, message: str, *, field: str | None = None):
        super().__init__(message)
        self.field = field


class ApplePayEventConflict(ValueError):
    """El mismo event_id llegó con un contenido diferente."""


def autenticar_api_key(secreto: str | None) -> ApplePayApiKey | None:
    """Resuelve una API key sin guardar ni comparar secretos en texto plano."""
    secreto = (secreto or "").strip()
    if not secreto:
        return None
    clave_hash = ApplePayApiKey.hash_clave(secreto)
    api_key = (
        ApplePayApiKey.objects.select_related("user")
        .filter(
            clave_hash=clave_hash,
            activo=True,
            revocado_at__isnull=True,
            user__is_active=True,
        )
        .first()
    )
    if api_key:
        api_key.ultimo_uso_at = timezone.now()
        api_key.save(update_fields=["ultimo_uso_at", "updated_at"])
    return api_key


def _decimal(value, *, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ApplePayPayloadError(f"{field} debe ser un número válido.", field=field)
    if not result.is_finite():
        raise ApplePayPayloadError(f"{field} debe ser un número finito.", field=field)
    return result


def _parse_fecha(value):
    if value in (None, ""):
        return timezone.localdate()
    if not isinstance(value, str):
        raise ApplePayPayloadError("occurred_at debe ser una fecha ISO-8601.", field="occurred_at")
    instante = parse_datetime(value)
    if instante:
        if timezone.is_naive(instante):
            instante = timezone.make_aware(instante, timezone.get_current_timezone())
        return timezone.localdate(instante)
    fecha = parse_date(value)
    if fecha:
        return fecha
    raise ApplePayPayloadError("occurred_at debe ser una fecha ISO-8601.", field="occurred_at")


def _as_bool(value, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _validar_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ApplePayPayloadError("El cuerpo debe ser un objeto JSON.")

    event_id = str(payload.get("event_id") or "").strip()
    if not event_id:
        raise ApplePayPayloadError(
            "event_id es obligatorio para evitar duplicados al reintentar el Atajo.",
            field="event_id",
        )
    if len(event_id) > 120:
        raise ApplePayPayloadError("event_id supera 120 caracteres.", field="event_id")

    descripcion = str(payload.get("merchant") or payload.get("descripcion") or "").strip()
    if not descripcion:
        raise ApplePayPayloadError("merchant es obligatorio.", field="merchant")
    if len(descripcion) > 255:
        raise ApplePayPayloadError("merchant supera 255 caracteres.", field="merchant")

    monto = _decimal(payload.get("amount"), field="amount")
    if monto <= 0:
        raise ApplePayPayloadError("amount debe ser mayor que cero.", field="amount")
    if monto > MAX_AMOUNT:
        raise ApplePayPayloadError("amount supera el máximo permitido.", field="amount")

    moneda = str(payload.get("currency") or "CLP").upper().strip()
    if moneda not in SUPPORTED_CURRENCIES:
        raise ApplePayPayloadError("currency debe ser CLP o USD.", field="currency")

    amount_clp = payload.get("amount_clp")
    tipo_cambio = payload.get("exchange_rate")
    if amount_clp not in (None, ""):
        monto_clp = _decimal(amount_clp, field="amount_clp")
    elif moneda == "CLP":
        monto_clp = monto
    elif tipo_cambio not in (None, ""):
        monto_clp = (monto * _decimal(tipo_cambio, field="exchange_rate")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    else:
        raise ApplePayPayloadError(
            "Para pagos USD envía amount_clp o exchange_rate.", field="amount_clp"
        )
    if monto_clp <= 0 or monto_clp > MAX_AMOUNT:
        raise ApplePayPayloadError("amount_clp está fuera de rango.", field="amount_clp")

    card_last4 = str(payload.get("card_last4") or "").strip()
    if card_last4 and (len(card_last4) != 4 or not card_last4.isdigit()):
        raise ApplePayPayloadError("card_last4 debe tener exactamente 4 dígitos.", field="card_last4")

    product_id = payload.get("producto_id", payload.get("product_id"))
    if product_id not in (None, ""):
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            raise ApplePayPayloadError("product_id debe ser un entero.", field="product_id")
        if product_id <= 0:
            raise ApplePayPayloadError("product_id debe ser positivo.", field="product_id")

    return {
        "event_id": event_id,
        "descripcion": descripcion,
        "monto": monto,
        "moneda": moneda,
        "monto_clp": monto_clp,
        "fecha": _parse_fecha(payload.get("occurred_at", payload.get("fecha"))),
        "card_last4": card_last4,
        "product_id": product_id,
        "notificar": _as_bool(payload.get("notify"), default=True),
    }


def _payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resolver_producto(product_id: int | None, card_last4: str):
    from configuracion.models import Banco, Producto

    if product_id:
        producto = Producto.objects.filter(pk=product_id, activo=True).first()
        if not producto:
            raise ApplePayPayloadError("El producto indicado no existe o está inactivo.", field="product_id")
        return producto

    if card_last4:
        producto = (
            Producto.objects.filter(activo=True, nombre__icontains=card_last4)
            .order_by("id")
            .first()
        )
        if producto:
            return producto

    banco, _ = Banco.objects.get_or_create(
        nombre="Apple Pay",
        defaults={"activo": True, "notas": "Producto creado por la POC del Atajo de Apple Pay."},
    )
    nombre = f"Apple Pay · *{card_last4}" if card_last4 else "Apple Pay · POC"
    producto, _ = Producto.objects.get_or_create(
        banco=banco,
        nombre=nombre,
        defaults={"tipo": "OTRO", "activo": True},
    )
    return producto


def _movement_hash(user, event_id: str) -> str:
    return hashlib.sha256(f"apple-pay:{user.pk}:{event_id}".encode("utf-8")).hexdigest()


def _movimiento_response(mov: MovimientoBancario, *, event_id: str) -> dict:
    categoria = mov.categoria.nombre if mov.categoria_id else None
    return {
        "id": mov.id,
        "event_id": event_id,
        "fecha": mov.fecha.isoformat(),
        "merchant": mov.descripcion,
        "amount": str(mov.monto),
        "currency": mov.moneda,
        "amount_clp": str(mov.monto_clp),
        "product_id": mov.producto_id,
        "category": categoria,
        "classification_source": mov.fuente_clasificacion if categoria else "PENDIENTE",
        "classification_confidence": str(mov.confianza_ia) if mov.confianza_ia is not None else None,
        "notified_telegram": mov.notificado_telegram,
    }


@transaction.atomic
def registrar_pago(user, payload: dict, *, api_key: ApplePayApiKey | None = None, clasificador=None) -> dict:
    """Persiste y procesa un pago de Apple Pay de forma idempotente."""
    datos = _validar_payload(payload)
    payload_hash = _payload_hash(payload)
    event_id = datos["event_id"]

    evento = (
        ApplePayEvent.objects.select_related("movimiento", "movimiento__categoria")
        .filter(user=user, event_id=event_id)
        .first()
    )
    if evento:
        if evento.payload_hash != payload_hash:
            raise ApplePayEventConflict(
                "event_id ya fue usado con un payload diferente; no se modificó el movimiento."
            )
        if evento.movimiento:
            return {
                "created": False,
                "duplicated": True,
                "movement": _movimiento_response(evento.movimiento, event_id=event_id),
                "classification": {
                    "source": evento.movimiento.fuente_clasificacion
                    if evento.movimiento.categoria_id
                    else "PENDIENTE",
                    "category": evento.movimiento.categoria.nombre
                    if evento.movimiento.categoria_id
                    else None,
                },
                "notification": {"telegram_sent": evento.movimiento.notificado_telegram},
            }

    producto = _resolver_producto(datos["product_id"], datos["card_last4"])
    movimiento, created = MovimientoBancario.objects.get_or_create(
        user=user,
        hash_externo=_movement_hash(user, event_id),
        defaults={
            "producto": producto,
            "fecha": datos["fecha"],
            "descripcion": datos["descripcion"],
            "monto": datos["monto"],
            "moneda": datos["moneda"],
            "monto_clp": datos["monto_clp"],
            "estado": "NO_FACTURADO",
            "naturaleza": "OPERACIONAL",
        },
    )

    if not created:
        ApplePayEvent.objects.get_or_create(
            user=user,
            event_id=event_id,
            defaults={
                "api_key": api_key,
                "payload_hash": payload_hash,
                "movimiento": movimiento,
                "estado": "DUPLICADO",
                "procesado_at": timezone.now(),
            },
        )
        return {
            "created": False,
            "duplicated": True,
            "movement": _movimiento_response(movimiento, event_id=event_id),
            "classification": {
                "source": movimiento.fuente_clasificacion
                if movimiento.categoria_id
                else "PENDIENTE",
                "category": movimiento.categoria.nombre if movimiento.categoria_id else None,
            },
            "notification": {"telegram_sent": movimiento.notificado_telegram},
        }

    clasificacion = {"regla": 0, "ia": 0, "sin_resolver": 0}
    try:
        from banca.services.clasificacion_ia import clasificar_pendientes

        clasificacion = clasificar_pendientes(
            user, ids=[movimiento.id], clasificador=clasificador
        )
    except Exception:  # pragma: no cover - la clasificación ya tiene fallback propio
        logger.exception("La clasificación del pago Apple Pay falló; queda pendiente.")

    telegram_sent = False
    if datos["notificar"]:
        try:
            from banca.services.notificador import avisar_nuevos_no_facturados

            telegram_sent = bool(avisar_nuevos_no_facturados(user, [movimiento.id]))
        except Exception:  # pragma: no cover - el envío no debe revertir el gasto
            logger.exception("El aviso del pago Apple Pay falló; el movimiento sí quedó guardado.")

    movimiento.refresh_from_db()
    respuesta = {
        "created": True,
        "duplicated": False,
        "movement": _movimiento_response(movimiento, event_id=event_id),
        "classification": {
            "source": movimiento.fuente_clasificacion if movimiento.categoria_id else "PENDIENTE",
            "category": movimiento.categoria.nombre if movimiento.categoria_id else None,
            "counts": clasificacion,
        },
        "notification": {"telegram_sent": telegram_sent},
    }
    ApplePayEvent.objects.create(
        user=user,
        api_key=api_key,
        event_id=event_id,
        payload_hash=payload_hash,
        movimiento=movimiento,
        estado="PROCESADO",
        respuesta=respuesta,
        processed_at=timezone.now(),
    )
    return respuesta
