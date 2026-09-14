"""Conector experimental de solo lectura para un endpoint autorizado de Houm."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from bank_sync.connectors.base import RentalManagerConnector
from bank_sync.exceptions import AuthError, ExtractionError, InteractiveAuthRequired, ParseError
from bank_sync.models import (
    DetalleLiquidacionNormalizado,
    LiquidacionArriendoNormalizada,
    ResultadoExtraccionExterna,
)


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
        "success": "CONFIRMADO", "paid": "CONFIRMADO", "deposited": "CONFIRMADO",
        "pagado": "CONFIRMADO", "depositado": "CONFIRMADO",
        "pending": "EN_PROCESO", "processing": "EN_PROCESO", "pendiente": "EN_PROCESO",
        "failed": "FALLIDO", "rejected": "FALLIDO", "rechazado": "FALLIDO",
        "reversed": "REVERSADO", "reversa": "REVERSADO",
    }.get(key, str(value or "CONFIRMADO").upper())


class HoumConnector(RentalManagerConnector):
    nombre = "houm"

    def __init__(self, **kwargs):
        self.base_url = (kwargs.get("base_url") or "").rstrip("/")
        self.settlements_endpoint = kwargs.get("settlements_endpoint") or "/landlord/settlements"
        self.properties_endpoint = kwargs.get("properties_endpoint") or "/landlord/properties"
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
                "Houm automático requiere un endpoint autorizado y token de solo lectura; "
                "usa el informe XLSX oficial mientras no estén disponibles."
            )

    def _get(self, endpoint, params=None):
        response = self.http.get(
            f"{self.base_url}{endpoint}",
            params=params or {},
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=30,
        )
        if response.status_code in (401, 403):
            raise AuthError("La sesión de Houm expiró o no tiene permiso de lectura.")
        if response.status_code >= 400:
            raise ExtractionError(f"Houm respondió HTTP {response.status_code}.")
        payload = response.json() or {}
        if str(payload.get("challenge") or payload.get("action_required") or "").lower() in {"captcha", "mfa", "2fa", "reauthenticate"}:
            raise InteractiveAuthRequired("Houm requiere CAPTCHA, MFA o renovar la sesión manualmente.")
        return payload

    def get_properties(self) -> list[dict]:
        payload = self._get(self.properties_endpoint)
        rows = payload.get("properties", payload.get("data", []))
        if not isinstance(rows, list):
            raise ParseError("Respuesta Houm sin una lista de propiedades.")
        return rows

    def get_settlements(self, *, periodo=None, cursor="") -> ResultadoExtraccionExterna:
        params = {}
        if periodo:
            params["period"] = periodo.strftime("%Y-%m")
        if cursor:
            params["cursor"] = cursor
        payload = self._get(self.settlements_endpoint, params)
        rows = payload.get("settlements", payload.get("data", []))
        if not isinstance(rows, list):
            raise ParseError("Respuesta Houm sin una lista de liquidaciones.")
        liquidaciones = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            external_id = str(row.get("id") or row.get("settlement_id") or "").strip()
            property_id = str(row.get("property_id") or row.get("propertyId") or "").strip()
            period = _date(row.get("period") or row.get("periodo")) or periodo
            if not external_id or not property_id or period is None:
                raise ParseError("Liquidación Houm sin ID, propiedad o período estable.")
            details = []
            for item in row.get("details", row.get("detalles", [])) or []:
                if isinstance(item, dict):
                    details.append(
                        DetalleLiquidacionNormalizado(
                            concepto=str(item.get("description") or item.get("concepto") or "Otro"),
                            monto=_decimal(item.get("amount") or item.get("monto")),
                            tipo=str(item.get("type") or item.get("tipo") or "OTRO").upper(),
                            metadata=item,
                        )
                    )
            liquidaciones.append(
                LiquidacionArriendoNormalizada(
                    external_id=external_id,
                    propiedad_id=property_id,
                    propiedad_nombre=str(row.get("property_name") or row.get("propiedad") or property_id),
                    periodo=period.replace(day=1),
                    renta_bruta=_decimal(row.get("gross_rent") or row.get("renta_bruta")),
                    deposito_neto=_decimal(row.get("net_deposit") or row.get("deposito_neto")),
                    estado=_status(row.get("status") or row.get("estado")),
                    fecha_deposito=_date(row.get("deposit_date") or row.get("fecha_deposito")),
                    comision=_decimal(row.get("commission") or row.get("comision")),
                    iva=_decimal(row.get("vat") or row.get("iva")),
                    corretaje=_decimal(row.get("brokerage") or row.get("corretaje")),
                    promociones=_decimal(row.get("promotions") or row.get("promociones")),
                    reparaciones=_decimal(row.get("repairs") or row.get("reparaciones")),
                    servicios=_decimal(row.get("services") or row.get("servicios")),
                    ajustes=_decimal(row.get("adjustments") or row.get("ajustes")),
                    detalles=details,
                    metadata=row,
                )
            )
        return ResultadoExtraccionExterna(
            elementos=liquidaciones,
            cursor=str(payload.get("next_cursor") or payload.get("cursor") or ""),
            obtenido_at=datetime.now(),
        )
