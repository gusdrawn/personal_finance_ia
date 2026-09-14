"""
Registro de connectors por nombre de fuente.

Los connectors se cargan de forma perezosa para no importar Playwright ni SDKs de
exchanges a menos que realmente se use un connector que lo necesite.

Cada fuente declara su `tipo` ("banco" | "cripto" | "inversion" | "afp"), lo que permite
al orquestador decidir cómo ingerir sus datos:
  - "banco"  -> BankConnector  -> movimientos/deudas -> banca.MovimientoBancario/SaldoSnapshot
  - resto    -> AssetConnector -> posiciones          -> patrimonio.Activo/HistorialActivo
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from bank_sync.connectors.base import BankConnector
from bank_sync.exceptions import ConnectorNotFound

# tipos de fuente que soporta el orquestador de sincronización
TIPO_BANCO = "banco"
TIPOS_ACTIVO = ("cripto", "inversion", "afp")
TIPO_PAGOS = "pagos"
TIPO_ADMINISTRACION_INMOBILIARIA = "administracion_inmobiliaria"
TIPOS_EXTERNOS = (TIPO_PAGOS, TIPO_ADMINISTRACION_INMOBILIARIA)


@dataclass
class _Entry:
    #: función que devuelve la CLASE del connector (import perezoso)
    loader: Callable[[], type]
    tipo: str = TIPO_BANCO
    capabilities: tuple[str, ...] = field(default_factory=tuple)


# nombre_fuente -> _Entry
_REGISTRY: dict[str, _Entry] = {}


def register_connector(
    nombre: str,
    loader: Callable[[], type],
    *,
    tipo: str = TIPO_BANCO,
    capabilities: tuple[str, ...] = (),
) -> None:
    _REGISTRY[nombre.lower()] = _Entry(
        loader=loader, tipo=tipo, capabilities=tuple(capabilities)
    )


def available_connectors() -> list[str]:
    return sorted(_REGISTRY.keys())


def connectors_by_tipo(tipo: str) -> list[str]:
    """Nombres de connectors registrados con el `tipo` dado."""
    return sorted(k for k, e in _REGISTRY.items() if e.tipo == tipo)


def connector_tipo(nombre: str) -> str:
    """Devuelve el `tipo` de una fuente registrada."""
    entry = _REGISTRY.get((nombre or "").lower())
    if entry is None:
        raise ConnectorNotFound(
            f"No hay connector para '{nombre}'. Disponibles: {available_connectors()}"
        )
    return entry.tipo


def es_fuente_activos(nombre: str) -> bool:
    """True si la fuente reporta posiciones (cripto/inversión/AFP) en vez de deuda."""
    return connector_tipo(nombre) in TIPOS_ACTIVO


def es_fuente_externa(nombre: str) -> bool:
    """True para plataformas que enriquecen movimientos sin reemplazarlos."""
    return connector_tipo(nombre) in TIPOS_EXTERNOS


def get_connector(nombre: str, **kwargs):
    """
    Instancia el connector de la fuente pedida. kwargs van al __init__.

    Devuelve un `BankConnector` o un `AssetConnector` según el tipo de fuente; el
    llamador decide qué métodos usar (ver `connector_tipo` / `es_fuente_activos`).
    """
    key = (nombre or "").lower()
    entry = _REGISTRY.get(key)
    if entry is None:
        raise ConnectorNotFound(
            f"No hay connector para '{nombre}'. Disponibles: {available_connectors()}"
        )
    cls = entry.loader()
    return cls(**kwargs)


# --- bancos -------------------------------------------------------------------------
def _load_santander() -> type[BankConnector]:
    from bank_sync.connectors.santander import SantanderConnector

    return SantanderConnector


register_connector(
    "santander",
    _load_santander,
    tipo=TIPO_BANCO,
    capabilities=("deudas", "no_facturado", "estado_cuenta"),
)


# --- cripto / exchanges (fuentes de activos, API oficial) ---------------------------
def _load_binance() -> type:
    from bank_sync.connectors.binance import BinanceConnector

    return BinanceConnector


def _load_buda() -> type:
    from bank_sync.connectors.buda import BudaConnector

    return BudaConnector


def _load_cryptomkt() -> type:
    from bank_sync.connectors.cryptomkt import CryptoMktConnector

    return CryptoMktConnector


def _load_bitcoin_wallet() -> type:
    from bank_sync.connectors.bitcoin_wallet import BitcoinWalletConnector

    return BitcoinWalletConnector


register_connector(
    "binance", _load_binance, tipo="cripto", capabilities=("posiciones",)
)
register_connector("buda", _load_buda, tipo="cripto", capabilities=("posiciones",))
register_connector(
    "cryptomkt", _load_cryptomkt, tipo="cripto", capabilities=("posiciones",)
)
register_connector(
    "bitcoin_wallet", _load_bitcoin_wallet, tipo="cripto", capabilities=("posiciones",)
)


# --- inversiones (API oficial) ------------------------------------------------------
def _load_fintual() -> type:
    from bank_sync.connectors.fintual import FintualConnector

    return FintualConnector


register_connector(
    "fintual", _load_fintual, tipo="inversion", capabilities=("posiciones",)
)


# Houm y Neat se registran aunque el acceso automático permanezca experimental: sus
# importadores oficiales usan los mismos nombres y el mismo pipeline normalizado.
def _load_neat() -> type:
    from bank_sync.connectors.neat import NeatConnector

    return NeatConnector


def _load_houm() -> type:
    from bank_sync.connectors.houm import HoumConnector

    return HoumConnector


register_connector(
    "neat",
    _load_neat,
    tipo=TIPO_PAGOS,
    capabilities=("pagos", "importacion", "lectura_experimental"),
)
register_connector(
    "houm",
    _load_houm,
    tipo=TIPO_ADMINISTRACION_INMOBILIARIA,
    capabilities=("propiedades", "liquidaciones", "importacion", "lectura_experimental"),
)
