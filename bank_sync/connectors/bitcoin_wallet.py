"""
Connector de wallet Bitcoin (solo lectura, sin credenciales).

No usa API key: recibe una **dirección pública o xpub** (`identificador_publico`) y consulta
el saldo en un explorador público. Por defecto blockchain.info, que soporta tanto
direcciones como xpub vía `/balance?active=...` (saldo en satoshis).

El saldo BTC se valoriza en USD con `bank_sync.precios`.

Nota de privacidad: solo se consulta información pública de la blockchain; nunca se envían
claves. Se pueden separar varias direcciones/xpub con `|`.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from bank_sync.connectors.base import AssetConnector
from bank_sync.exceptions import AuthError, ParseError
from bank_sync.precios import precio_usd
from bank_sync.models import PosicionNormalizada

logger = logging.getLogger("bank_sync.bitcoin_wallet")

API_BASE = "https://blockchain.info"
SATOSHIS = Decimal("100000000")  # 1 BTC = 1e8 sat


class BitcoinWalletConnector(AssetConnector):
    nombre = "bitcoin_wallet"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.base_url = kwargs.get("base_url", API_BASE)
        self._session = kwargs.get("session")
        self.direccion = ""

    @property
    def http(self):
        if self._session is not None:
            return self._session
        import requests

        return requests

    def authenticate(self, **creds) -> None:
        # sin secreto: solo la dirección/xpub pública
        self.direccion = (creds.get("identificador_publico") or "").strip()
        if not self.direccion:
            raise AuthError(
                "La wallet Bitcoin requiere una dirección o xpub pública "
                "(campo 'Dirección pública')."
            )

    def _saldo_btc(self) -> Decimal:
        resp = self.http.get(
            f"{self.base_url}/balance", params={"active": self.direccion}, timeout=20
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or not data:
            raise ParseError("Respuesta del explorador de Bitcoin inesperada.")
        # {addr_o_xpub: {"final_balance": satoshis, ...}, ...}
        satoshis = Decimal("0")
        for entry in data.values():
            if isinstance(entry, dict):
                satoshis += Decimal(str(entry.get("final_balance", 0) or 0))
        return satoshis / SATOSHIS

    def get_posiciones(self) -> list[PosicionNormalizada]:
        btc = self._saldo_btc()
        if btc <= 0:
            return []
        precio = precio_usd("BTC", session=self._session)
        return [
            PosicionNormalizada(
                simbolo="BTC",
                nombre="Bitcoin (wallet)",
                tipo_activo="CRIPTO",
                fuente="bitcoin_wallet",
                cantidad=btc,
                precio_unitario=precio,
                moneda="USD",
            )
        ]
