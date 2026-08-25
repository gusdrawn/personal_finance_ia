"""
Connector de CryptoMKT (fuente de activos, solo lectura).

API v3 (https://api.exchange.cryptomkt.com/api/3), autenticada con Basic auth
`base64(api_key:api_secret)` sobre HTTPS:
  - GET /api/3/spot/balance -> saldos de trading por moneda.
CryptoMKT cotiza en CLP; valorizamos cada tenencia con el ticker público
`/api/3/public/ticker/{SIMBOLO}CLP` (last).

La API key debe ser de solo lectura. Validación real con llaves reales en la máquina del
usuario; los tests cubren parseo + auth con HTTP mockeado.
"""

from __future__ import annotations

import base64
import logging
from decimal import Decimal

from bank_sync.connectors.base import AssetConnector
from bank_sync.exceptions import AuthError, ParseError
from bank_sync.models import PosicionNormalizada

logger = logging.getLogger("bank_sync.cryptomkt")

API_BASE = "https://api.exchange.cryptomkt.com/api/3"


def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


class CryptoMktConnector(AssetConnector):
    nombre = "cryptomkt"

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
            raise AuthError(
                "CryptoMKT requiere api_key y api_secret (de solo lectura)."
            )

    def _basic_header(self) -> dict:
        token = base64.b64encode(f"{self.api_key}:{self.api_secret}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def _auth_get(self, path: str):
        resp = self.http.get(
            f"{self.base_url}{path}", headers=self._basic_header(), timeout=20
        )
        if resp.status_code in (401, 403):
            raise AuthError("CryptoMKT rechazó la API key (¿permisos o credenciales?).")
        resp.raise_for_status()
        return resp.json()

    def _precio_clp(self, moneda: str) -> Decimal | None:
        try:
            resp = self.http.get(
                f"{self.base_url}/public/ticker/{moneda.upper()}CLP", timeout=20
            )
            resp.raise_for_status()
            data = resp.json()
            # /public/ticker/{symbol} devuelve un objeto con 'last'
            last = data.get("last") if isinstance(data, dict) else None
            return _to_decimal(last) if last is not None else None
        except Exception as e:  # pragma: no cover - red
            logger.warning("Sin precio CLP para %s en CryptoMKT: %s", moneda, e)
            return None

    def get_posiciones(self) -> list[PosicionNormalizada]:
        data = self._auth_get("/spot/balance")
        if not isinstance(data, list):
            raise ParseError("Respuesta de CryptoMKT (spot/balance) inesperada.")

        posiciones: list[PosicionNormalizada] = []
        for b in data:
            moneda = (b.get("currency") or "").upper()
            cantidad = _to_decimal(b.get("available")) + _to_decimal(b.get("reserved"))
            if not moneda or cantidad <= 0:
                continue
            if moneda == "CLP":
                posiciones.append(
                    PosicionNormalizada(
                        simbolo="CLP",
                        nombre="Saldo CLP (CryptoMKT)",
                        tipo_activo="EFECTIVO",
                        fuente="cryptomkt",
                        valor_clp=cantidad,
                        moneda="CLP",
                    )
                )
                continue
            precio = self._precio_clp(moneda)
            posiciones.append(
                PosicionNormalizada(
                    simbolo=moneda,
                    nombre=f"{moneda} (CryptoMKT)",
                    tipo_activo="CRIPTO",
                    fuente="cryptomkt",
                    cantidad=cantidad,
                    precio_unitario=precio,
                    moneda="CLP",
                )
            )
        return posiciones
