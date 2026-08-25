"""
Precios spot de cripto en USD, para valorizar posiciones.

Fuente por defecto: el endpoint público de Binance (`/api/v3/ticker/price`), que no
requiere API key y entrega todos los pares en una sola llamada. Los exchanges chilenos
(Buda, CryptoMKT) cotizan en CLP y valorizan con su propio ticker; este módulo se usa sobre
todo para Binance y para la wallet de Bitcoin (que no tiene exchange propio).

Puro `requests`, sin Django, para mantener `bank_sync` independiente y testeable.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Optional

logger = logging.getLogger("bank_sync.precios")

BINANCE_PUBLIC = "https://api.binance.com"

# equivalentes a 1 USD (stablecoins y el propio dólar)
STABLES_USD = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USD", "USDP", "FDUSD"}


def _to_decimal(value) -> Optional[Decimal]:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def precios_spot_usd(
    *, session=None, base_url: str = BINANCE_PUBLIC
) -> dict[str, Decimal]:
    """
    Devuelve {ASSET: precio_en_usd} para todos los pares ASSET/USDT de Binance.

    Una sola llamada trae el mercado completo; el connector luego busca cada asset. Si la
    llamada falla, devuelve un dict vacío (el connector puede seguir sin valorización).
    """
    http = session
    if http is None:
        import requests

        http = requests
    try:
        resp = http.get(f"{base_url}/api/v3/ticker/price", timeout=20)
        resp.raise_for_status()
        filas = resp.json()
    except Exception as e:  # pragma: no cover - red
        logger.warning("No se pudieron obtener precios spot: %s", e)
        return {}

    precios: dict[str, Decimal] = {}
    for fila in filas or []:
        symbol = (fila.get("symbol") or "") if isinstance(fila, dict) else ""
        if not symbol.endswith("USDT"):
            continue
        asset = symbol[: -len("USDT")]
        precio = _to_decimal(fila.get("price"))
        if asset and precio is not None:
            precios[asset] = precio
    return precios


def precio_usd(
    simbolo: str,
    *,
    precios: Optional[dict[str, Decimal]] = None,
    session=None,
    base_url: str = BINANCE_PUBLIC,
) -> Optional[Decimal]:
    """
    Precio en USD de un símbolo. Las stablecoins valen 1. Si `precios` (mapa precargado) no
    lo trae, consulta el ticker individual del par ASSET/USDT.
    """
    s = (simbolo or "").upper()
    if not s:
        return None
    if s in STABLES_USD:
        return Decimal("1")
    if precios and s in precios:
        return precios[s]

    http = session
    if http is None:
        import requests

        http = requests
    try:
        resp = http.get(
            f"{base_url}/api/v3/ticker/price", params={"symbol": f"{s}USDT"}, timeout=20
        )
        resp.raise_for_status()
        return _to_decimal(resp.json().get("price"))
    except Exception as e:  # pragma: no cover - red
        logger.warning("Sin precio USD para %s: %s", s, e)
        return None
