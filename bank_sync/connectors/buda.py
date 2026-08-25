"""
Connector de Buda.com (fuente de activos, solo lectura).

API v2 autenticada (https://api.buda.com/api/v2). Firma HMAC-SHA384 sobre
`"{METHOD} {path} {nonce}"` (para GET no hay cuerpo), con headers:
  X-SBTC-APIKEY, X-SBTC-NONCE, X-SBTC-SIGNATURE.

Buda cotiza en CLP, así que valorizamos cada tenencia con su propio ticker
`/markets/{cur}-clp/ticker` (last_price). El saldo en CLP se reporta como efectivo.

La API key debe ser de solo lectura. Validación real con llaves reales en la máquina del
usuario; los tests cubren parseo + firma con HTTP mockeado.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from decimal import Decimal

from bank_sync.connectors.base import AssetConnector
from bank_sync.exceptions import AuthError, ParseError
from bank_sync.models import PosicionNormalizada

logger = logging.getLogger("bank_sync.buda")

API_BASE = "https://api.buda.com/api/v2"


def _monto(par) -> Decimal:
    """Buda entrega montos como ['valor', 'MONEDA']; toma el valor."""
    if isinstance(par, (list, tuple)) and par:
        try:
            return Decimal(str(par[0]))
        except Exception:
            return Decimal("0")
    try:
        return Decimal(str(par))
    except Exception:
        return Decimal("0")


class BudaConnector(AssetConnector):
    nombre = "buda"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = kwargs.get("base_url", API_BASE)
        self._session = kwargs.get("session")
        self.api_key = ""
        self.api_secret = ""

    @property
    def http(self):
        if self._session is not None:
            return self._session
        import requests

        return requests

    def authenticate(self, **creds) -> None:
        self.api_key = (creds.get("api_key") or "").strip()
        self.api_secret = (creds.get("api_secret") or "").strip()
        if not self.api_key or not self.api_secret:
            raise AuthError("Buda requiere api_key y api_secret (de solo lectura).")

    def _headers(self, method: str, path: str, body_b64: str = "") -> dict:
        nonce = str(int(time.time() * 1_000_000))
        partes = [method.upper(), path, nonce]
        if body_b64:
            partes = [method.upper(), path, body_b64, nonce]
        mensaje = " ".join(partes)
        firma = hmac.new(
            self.api_secret.encode(), mensaje.encode(), hashlib.sha384
        ).hexdigest()
        return {
            "X-SBTC-APIKEY": self.api_key,
            "X-SBTC-NONCE": nonce,
            "X-SBTC-SIGNATURE": firma,
            "Content-Type": "application/json",
        }

    def _auth_get(self, path: str):
        resp = self.http.get(
            f"{self.base_url}{path}",
            headers=self._headers("GET", f"/api/v2{path}"),
            timeout=20,
        )
        if resp.status_code in (401, 403):
            raise AuthError("Buda rechazó la API key (¿permisos o firma?).")
        resp.raise_for_status()
        return resp.json()

    def _precio_clp(self, moneda: str) -> Decimal | None:
        """last_price del par {moneda}-CLP (público, sin firma)."""
        try:
            resp = self.http.get(
                f"{self.base_url}/markets/{moneda.lower()}-clp/ticker", timeout=20
            )
            resp.raise_for_status()
            ticker = resp.json().get("ticker", {})
            return _monto(ticker.get("last_price"))
        except Exception as e:  # pragma: no cover - red
            logger.warning("Sin precio CLP para %s en Buda: %s", moneda, e)
            return None

    def get_posiciones(self) -> list[PosicionNormalizada]:
        data = self._auth_get("/balances")
        balances = data.get("balances") if isinstance(data, dict) else None
        if balances is None:
            raise ParseError("Respuesta de Buda sin 'balances'.")

        posiciones: list[PosicionNormalizada] = []
        for b in balances:
            moneda = (b.get("id") or "").upper()
            cantidad = _monto(b.get("amount"))
            if not moneda or cantidad <= 0:
                continue
            if moneda == "CLP":
                posiciones.append(
                    PosicionNormalizada(
                        simbolo="CLP",
                        nombre="Saldo CLP (Buda)",
                        tipo_activo="EFECTIVO",
                        fuente="buda",
                        valor_clp=cantidad,
                        moneda="CLP",
                    )
                )
                continue
            precio = self._precio_clp(moneda)
            posiciones.append(
                PosicionNormalizada(
                    simbolo=moneda,
                    nombre=f"{moneda} (Buda)",
                    tipo_activo="CRIPTO",
                    fuente="buda",
                    cantidad=cantidad,
                    precio_unitario=precio,
                    moneda="CLP",
                )
            )
        return posiciones
