"""
Contrato común que TODO banco debe implementar.

Agregar un banco nuevo = crear una subclase de `BankConnector` y registrarla en
`bank_sync/registry.py`. Nada del resto del sistema cambia.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Optional

from bank_sync.models import (
    DeudaResumen,
    EstadoCuenta,
    MovimientoNormalizado,
    PosicionNormalizada,
    SaldoEfectivo,
    ResultadoExtraccionExterna,
)


class BankConnector(ABC):
    """Interfaz de extracción de un banco."""

    #: identificador corto del banco, p.ej. "santander"
    nombre: str = ""

    def __init__(
        self, *, headless: bool = True, storage_state_path: Optional[str] = None
    ):
        self.headless = headless
        self.storage_state_path = storage_state_path

    # --- ciclo de vida de la sesión ---
    @abstractmethod
    def login(self, rut: str, clave: str) -> None:
        """Autentica. Persiste la sesión si hay storage_state_path."""

    @abstractmethod
    def logout(self) -> None:
        """Cierra recursos (navegador, etc.)."""

    # --- datos ---
    @abstractmethod
    def get_deudas(self) -> DeudaResumen:
        """Saldos actuales: cada TDC (con cupo USD y TC del banco) + créditos."""

    @abstractmethod
    def get_movimientos_no_facturados(self) -> list[MovimientoNormalizado]:
        """Compras del mes en curso aún no facturadas (para alertas de presupuesto)."""

    @abstractmethod
    def get_estado_cuenta(self, periodo: Optional[date] = None) -> list[EstadoCuenta]:
        """
        Estado(s) de cuenta facturado(s). Si `periodo` es None, el último disponible.
        Devuelve una lista (una entrada por tarjeta).
        """

    # --- context manager por comodidad ---
    def __enter__(self) -> "BankConnector":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.logout()
        except Exception:
            pass


class AssetConnector(ABC):
    """
    Interfaz de una fuente de ACTIVOS (broker, exchange, wallet, AFP).

    A diferencia de `BankConnector` (deuda/crédito), estas fuentes reportan posiciones
    que alimentan `patrimonio.Activo` / `HistorialActivo`. La mayoría se autentica con
    API key/secret (o una dirección pública), no con RUT/clave, por eso el contrato usa
    `authenticate(**credenciales)` en vez de `login(rut, clave)`.

    Agregar una fuente nueva = crear una subclase y registrarla en `bank_sync/registry.py`
    con `tipo` ("cripto" | "inversion" | "afp").
    """

    #: identificador corto de la fuente, p.ej. "binance"
    nombre: str = ""

    def __init__(self, **kwargs):
        # Las subclases guardan lo que necesiten (api_key, secret, dirección, etc.).
        self.opciones = kwargs

    @abstractmethod
    def authenticate(self, **credenciales) -> None:
        """
        Prepara el acceso a la fuente. Recibe las credenciales relevantes por keyword
        (p.ej. api_key/api_secret, o identificador_publico para wallets). Las fuentes
        de solo lectura por dirección pública pueden dejarlo como no-op.
        """

    def close(self) -> None:
        """Cierra recursos si los hay (sesión HTTP, navegador). Opcional."""

    # --- datos ---
    @abstractmethod
    def get_posiciones(self) -> list[PosicionNormalizada]:
        """Tenencias actuales de la fuente (una por instrumento/moneda)."""

    def get_efectivo(self) -> list[SaldoEfectivo]:
        """Saldo de caja no invertido, si la fuente lo expone. Por defecto vacío."""
        return []

    # --- context manager por comodidad ---
    def __enter__(self) -> "AssetConnector":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.close()
        except Exception:
            pass


class PaymentPlatformConnector(ABC):
    """Contrato de solo lectura para plataformas de pago como NeatPagos."""

    nombre: str = ""
    solo_lectura: bool = True

    @abstractmethod
    def authenticate(self, **credenciales) -> None:
        """Obtiene o restaura una sesión sin iniciar pagos."""

    @abstractmethod
    def get_payments(
        self, *, desde: Optional[date] = None, cursor: str = ""
    ) -> ResultadoExtraccionExterna:
        """Devuelve historial de pagos y el cursor de continuación."""

    def close(self) -> None:
        return None


class RentalManagerConnector(ABC):
    """Contrato de solo lectura para administradores inmobiliarios como Houm."""

    nombre: str = ""
    solo_lectura: bool = True

    @abstractmethod
    def authenticate(self, **credenciales) -> None:
        """Obtiene o restaura una sesión de propietario."""

    @abstractmethod
    def get_properties(self) -> list[dict]:
        """Lista identificadores y nombres de propiedades visibles."""

    @abstractmethod
    def get_settlements(
        self, *, periodo: Optional[date] = None, cursor: str = ""
    ) -> ResultadoExtraccionExterna:
        """Devuelve liquidaciones mensuales y el cursor de continuación."""

    def close(self) -> None:
        return None
