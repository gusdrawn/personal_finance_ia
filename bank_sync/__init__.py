"""
bank_sync — Paquete de extracción bancaria independiente de Django.

Cada banco implementa la interfaz `BankConnector` (connectors/base.py) y devuelve
objetos normalizados (models.py). El resto del sistema (la app Django `banca`) consume
esos objetos sin saber de qué banco vienen ni cómo se obtuvieron.

Uso típico:

    from bank_sync.registry import get_connector

    connector = get_connector("santander")
    connector.login(rut="12.345.678-9", clave="****")
    deudas = connector.get_deudas()
    movs = connector.get_movimientos_no_facturados()
    connector.logout()
"""

from bank_sync.models import (
    MovimientoNormalizado,
    SaldoTarjeta,
    SaldoHipotecario,
    DeudaResumen,
    EstadoCuenta,
    PosicionNormalizada,
    SaldoEfectivo,
)
from bank_sync.registry import (
    get_connector,
    register_connector,
    available_connectors,
    connectors_by_tipo,
    connector_tipo,
)

__all__ = [
    "MovimientoNormalizado",
    "SaldoTarjeta",
    "SaldoHipotecario",
    "DeudaResumen",
    "EstadoCuenta",
    "PosicionNormalizada",
    "SaldoEfectivo",
    "get_connector",
    "register_connector",
    "available_connectors",
    "connectors_by_tipo",
    "connector_tipo",
]
