"""
Connector de Fintual (fuente de inversiones, solo lectura).

Usa la API oficial (https://fintual.cl/api):
  1. POST /access_tokens con email + password -> token de acceso.
  2. GET /goals?user_email=...&user_token=... -> cada meta (fondo) con su valor actual (nav).

Cada meta se mapea a una posición `FONDO_MUTUO` valorizada en CLP. Fintual se autentica con
**email + contraseña** (no hay API key): en el formulario, el campo "API key" es el email y
"API secret" la contraseña.

Validación real con credenciales reales en la máquina del usuario; los tests cubren el flujo
de token + parseo de metas con HTTP mockeado.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from bank_sync.connectors.base import AssetConnector
from bank_sync.exceptions import AuthError, ParseError
from bank_sync.models import PosicionNormalizada

logger = logging.getLogger("bank_sync.fintual")

API_BASE = "https://fintual.cl/api"


def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


class FintualConnector(AssetConnector):
    nombre = "fintual"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = kwargs.get("base_url", API_BASE)
        self._session = kwargs.get("session")
        self.email = ""
        self.token = ""

    @property
    def http(self):
        if self._session is not None:
            return self._session
        import requests

        return requests

    def authenticate(self, **creds) -> None:
        # email/password explícitos, o api_key=email + api_secret=password (form genérico)
        self.email = (creds.get("email") or creds.get("api_key") or "").strip()
        password = creds.get("password") or creds.get("api_secret") or ""
        if not self.email or not password:
            raise AuthError("Fintual requiere email y contraseña.")

        resp = self.http.post(
            f"{self.base_url}/access_tokens",
            json={"user": {"email": self.email, "password": password}},
            timeout=20,
        )
        if resp.status_code in (401, 403):
            raise AuthError("Fintual rechazó las credenciales (email/contraseña).")
        resp.raise_for_status()
        data = resp.json() or {}
        self.token = (data.get("data", {}).get("attributes", {}) or {}).get("token", "")
        if not self.token:
            raise AuthError("Fintual no devolvió token de acceso.")

    def get_posiciones(self) -> list[PosicionNormalizada]:
        resp = self.http.get(
            f"{self.base_url}/goals",
            params={"user_email": self.email, "user_token": self.token},
            timeout=20,
        )
        resp.raise_for_status()
        payload = resp.json() or {}
        metas = payload.get("data")
        if metas is None:
            raise ParseError("Respuesta de Fintual (goals) sin 'data'.")

        posiciones: list[PosicionNormalizada] = []
        for meta in metas:
            attrs = meta.get("attributes", {}) if isinstance(meta, dict) else {}
            nombre = attrs.get("name") or f"Meta {meta.get('id', '')}"
            # `nav` (net asset value) es el valor actual de la meta en CLP
            valor = _to_decimal(attrs.get("nav"))
            if valor <= 0:
                continue
            posiciones.append(
                PosicionNormalizada(
                    simbolo=nombre,
                    nombre=f"{nombre} (Fintual)",
                    tipo_activo="FONDO_MUTUO",
                    fuente="fintual",
                    valor_clp=valor,
                    moneda="CLP",
                    horizonte="LARGO_PLAZO",
                    es_liquido=True,
                )
            )
        return posiciones
