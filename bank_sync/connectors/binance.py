"""
Connector de Binance (fuente de activos, solo lectura).

Usa la API REST oficial firmada con HMAC-SHA256:
  - GET /api/v3/account  -> balances por asset (free + locked).
Valoriza cada asset en USD con el ticker público (bank_sync.precios).

La API key debe ser **de solo lectura** (sin permisos de trading ni retiro). No abre
navegador: es una llamada HTTP directa.

Nota de puesta en marcha: la firma y los endpoints siguen la doc oficial de Binance. La
validación real se hace en la máquina del usuario con llaves reales (como el login de
Santander); los tests cubren el parseo y que la firma se adjunte, con HTTP mockeado.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from decimal import Decimal
from urllib.parse import urlencode

from bank_sync.connectors.base import AssetConnector
from bank_sync.exceptions import AuthError, ParseError
from bank_sync.models import PosicionNormalizada
from bank_sync.precios import STABLES_USD, precios_spot_usd

logger = logging.getLogger("bank_sync.binance")

API_BASE = "https://api.binance.com"

# Se omiten tenencias cuyo valor sea menor a esto (polvo/dust que ensucia el patrimonio).
DUST_USD = Decimal("1")


class BinanceConnector(AssetConnector):
    nombre = "binance"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # base_url y session inyectables para tests / entornos
        self.base_url = kwargs.get("base_url", API_BASE)
        self._session = kwargs.get("session")
        self.api_key = ""
        self.api_secret = ""

    # --- HTTP ---
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
            raise AuthError("Binance requiere api_key y api_secret (de solo lectura).")

    def _signed_get(self, path: str, params: dict | None = None):
        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        params.setdefault("recvWindow", 5000)
        query = urlencode(params)
        firma = hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()
        url = f"{self.base_url}{path}?{query}&signature={firma}"
        resp = self.http.get(url, headers={"X-MBX-APIKEY": self.api_key}, timeout=20)
        if resp.status_code in (401, 403):
            raise AuthError("Binance rechazó la API key (¿permisos o firma?).")
        resp.raise_for_status()
        return resp.json()

    # --- datos ---
    def get_posiciones(self) -> list[PosicionNormalizada]:
        data = self._signed_get("/api/v3/account")
        balances = data.get("balances") if isinstance(data, dict) else None
        if balances is None:
            raise ParseError("Respuesta de Binance sin 'balances'.")

        precios = precios_spot_usd(session=self._session, base_url=API_BASE)
        posiciones: list[PosicionNormalizada] = []
        for b in balances:
            asset = (b.get("asset") or "").upper()
            free = Decimal(str(b.get("free", "0") or "0"))
            locked = Decimal(str(b.get("locked", "0") or "0"))
            cantidad = free + locked
            if not asset or cantidad <= 0:
                continue

            base, precio, es_earn = self._precio_asset(asset, precios)
            # descartar polvo / assets sin precio (valor USD despreciable)
            valor_usd = cantidad * precio if precio is not None else Decimal("0")
            if valor_usd < DUST_USD:
                logger.info(
                    "Binance: se omite %s (valor %s USD < %s)",
                    asset,
                    valor_usd,
                    DUST_USD,
                )
                continue

            sufijo = " Earn (Binance)" if es_earn else " (Binance)"
            posiciones.append(
                PosicionNormalizada(
                    simbolo=base,
                    nombre=f"{base}{sufijo}",
                    tipo_activo="CRIPTO",
                    fuente="binance",
                    cantidad=cantidad,
                    precio_unitario=precio,
                    moneda="USD",
                )
            )
        return posiciones

    @staticmethod
    def _precio_asset(asset: str, precios: dict) -> tuple[str, "Decimal | None", bool]:
        """
        Devuelve (símbolo_base, precio_usd, es_earn).

        Maneja los saldos de Binance Earn / Ahorro Flexible, que vienen prefijados con `LD`
        (p.ej. `LDBTC` = BTC en Flexible Savings) y no tienen par de trading propio. Solo se
        despoja el prefijo si el activo NO tiene precio directo pero su subyacente sí — así
        `LDBTC`->`BTC` funciona sin romper tokens reales que empiezan con LD (ej. LDO, que sí
        tiene par LDOUSDT).
        """
        if asset in STABLES_USD:
            return asset, Decimal("1"), False
        if asset in precios:
            return asset, precios[asset], False
        if asset.startswith("LD"):
            base = asset[2:]
            if base in STABLES_USD:
                return base, Decimal("1"), True
            if base in precios:
                return base, precios[base], True
        return asset, None, False
