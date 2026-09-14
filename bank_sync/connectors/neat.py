"""Conector experimental de solo lectura para un endpoint autorizado de NeatPagos.

Neat no publica un contrato para usuarios. Por eso no se fija aquí ninguna URL privada:
el administrador debe proporcionar una ``base_url`` y un token de solo lectura obtenidos
por una vía autorizada. Sin ellos, el importador de archivos sigue siendo el canal estable.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from bank_sync.connectors.base import PaymentPlatformConnector
from bank_sync.exceptions import AuthError, ExtractionError, InteractiveAuthRequired, ParseError
from bank_sync.models import PagoExternoNormalizado, ResultadoExtraccionExterna


def _date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def _decimal(value) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return Decimal("0")


def _status(value) -> str:
    key = str(value or "").strip().lower().replace(" ", "_")
    return {
        "success": "CONFIRMADO", "successful": "CONFIRMADO", "paid": "CONFIRMADO",
        "exitoso": "CONFIRMADO", "pagado": "CONFIRMADO",
        "pending": "EN_PROCESO", "processing": "EN_PROCESO", "pendiente": "EN_PROCESO",
        "failed": "FALLIDO", "rejected": "FALLIDO", "rechazado": "FALLIDO",
        "reversed": "REVERSADO", "refunded": "REVERSADO", "reversa": "REVERSADO",
    }.get(key, str(value or "EN_PROCESO").upper())


class NeatConnector(PaymentPlatformConnector):
    nombre = "neat"

    def __init__(self, **kwargs):
        self.base_url = (kwargs.get("base_url") or "").rstrip("/")
        self.endpoint = kwargs.get("endpoint") or "/payments"
        self._session = kwargs.get("session")
        self.token = ""

    @property
    def http(self):
        if self._session is not None:
            return self._session
        import requests

        return requests

    def authenticate(self, **credenciales) -> None:
        self.token = (credenciales.get("access_token") or credenciales.get("api_key") or "").strip()
        if not self.base_url or not self.token:
            raise AuthError(
                "Neat automático requiere un endpoint autorizado y token de solo lectura; "
                "usa la importación mientras no estén disponibles."
            )

    def get_payments(self, *, desde=None, cursor="") -> ResultadoExtraccionExterna:
        params = {}
        if desde:
            params["from"] = desde.isoformat()
        if cursor:
            params["cursor"] = cursor
        response = self.http.get(
            f"{self.base_url}{self.endpoint}",
            params=params,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=30,
        )
        if response.status_code in (401, 403):
            raise AuthError("La sesión de Neat expiró o no tiene permiso de lectura.")
        if response.status_code >= 400:
            raise ExtractionError(f"Neat respondió HTTP {response.status_code}.")
        payload = response.json() or {}
        if str(payload.get("challenge") or payload.get("action_required") or "").lower() in {"captcha", "mfa", "2fa", "reauthenticate"}:
            raise InteractiveAuthRequired("Neat requiere CAPTCHA, MFA o renovar la sesión manualmente.")
        rows = payload.get("payments", payload.get("data", []))
        if not isinstance(rows, list):
            raise ParseError("Respuesta Neat sin una lista de pagos.")
        pagos = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            external_id = str(row.get("id") or row.get("payment_id") or row.get("paymentId") or "").strip()
            if not external_id:
                raise ParseError("Pago Neat sin identificador estable.")
            pagos.append(
                PagoExternoNormalizado(
                    external_id=external_id,
                    estado=_status(row.get("status") or row.get("estado")),
                    monto_pagado=_decimal(row.get("amount") or row.get("monto")),
                    comision=_decimal(row.get("fee") or row.get("commission") or row.get("comision")),
                    descuentos=_decimal(row.get("discount") or row.get("descuentos")),
                    cuenta=str(row.get("account") or row.get("cuenta") or ""),
                    tipo_cuenta=str(row.get("account_type") or row.get("tipo_cuenta") or ""),
                    destinatario=str(row.get("recipient") or row.get("destinatario") or ""),
                    tarjeta_ultimos4=str(row.get("card_last4") or row.get("tarjeta_ultimos4") or "")[-4:],
                    fecha_creacion=_date(row.get("created_at") or row.get("fecha")),
                    fecha_cargo=_date(row.get("charge_date") or row.get("fecha_cargo")),
                    fecha_transferencia_esperada=_date(row.get("expected_transfer_date")),
                    fecha_transferencia=_date(row.get("transfer_date") or row.get("fecha_transferencia")),
                    comprobante_referencia=str(row.get("receipt_id") or row.get("comprobante") or ""),
                    metadata=row,
                )
            )
        return ResultadoExtraccionExterna(
            elementos=pagos,
            cursor=str(payload.get("next_cursor") or payload.get("cursor") or ""),
            obtenido_at=datetime.now(),
        )
