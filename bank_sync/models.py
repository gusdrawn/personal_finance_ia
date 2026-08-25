"""
Objetos normalizados que todo connector devuelve, independientes del banco.

Son dataclasses puras (sin Django) para que la extracción sea testeable y reutilizable.
La app `banca` los mapea luego a sus modelos del ORM.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional


def _norm(texto: str) -> str:
    """Normaliza una glosa para huellas/calce: minúsculas, sin espacios extra."""
    return " ".join((texto or "").lower().split())


@dataclass
class MovimientoNormalizado:
    """Una transacción individual, en cualquier etapa de su ciclo de vida."""

    fecha: date  # fecha de compra (devengo)
    descripcion: str  # glosa / comercio tal como viene del banco
    monto: Decimal  # en la moneda original (positivo=cargo, negativo=abono)
    moneda: str  # "CLP" | "USD"
    estado: str  # "NO_FACTURADO" | "FACTURADO"
    tarjeta_id: Optional[str] = None  # últimos 4 dígitos / identificador
    cuota_actual: Optional[int] = None
    cuota_total: Optional[int] = None
    periodo_factura: Optional[date] = None  # a qué estado de cuenta pertenece
    tipo_banco: str = ""  # categoría/ícono que entrega el banco (informativo)

    @property
    def es_cuota(self) -> bool:
        return bool(self.cuota_total and self.cuota_total > 1)

    def hash_externo(self) -> str:
        """
        Huella ESTABLE entre NO_FACTURADO y FACTURADO: no incluye el estado ni el
        período, solo lo que no cambia al facturarse. Permite reconciliar (upsert)
        en vez de duplicar.
        """
        base = "|".join(
            [
                self.tarjeta_id or "",
                self.fecha.isoformat(),
                f"{self.monto:.2f}",
                self.moneda,
                _norm(self.descripcion),
                f"{self.cuota_actual or 0}/{self.cuota_total or 0}",
            ]
        )
        return hashlib.sha256(base.encode("utf-8")).hexdigest()


@dataclass
class SaldoTarjeta:
    """Foto de cupos y deuda de una tarjeta de crédito."""

    tarjeta_id: str
    nombre: str
    deuda_facturada_clp: Decimal = Decimal("0")
    deuda_no_facturada_clp: Decimal = Decimal("0")
    cupo_total_clp: Decimal = Decimal("0")
    cupo_disponible_clp: Decimal = Decimal("0")
    # --- dólares ---
    tiene_cupo_usd: bool = False
    cupo_usd: Optional[Decimal] = None
    cupo_usd_disponible: Optional[Decimal] = None
    deuda_usd: Optional[Decimal] = None
    tipo_cambio_banco: Optional[Decimal] = (
        None  # precio al que el banco te vende el USD
    )


@dataclass
class SaldoHipotecario:
    """Foto de la deuda de un crédito (hipotecario / consumo)."""

    nombre: str = "Crédito Hipotecario"
    saldo_insoluto: Decimal = Decimal("0")  # en CLP si moneda=='CLP'
    saldo_insoluto_uf: Optional[Decimal] = None  # saldo en UF (hipotecarios en UF)
    moneda: str = "CLP"  # "UF" | "CLP"
    proxima_cuota_clp: Decimal = Decimal("0")
    proxima_cuota_uf: Optional[Decimal] = None
    fecha_proximo_pago: Optional[date] = None
    tasa: Optional[Decimal] = None
    total_cuotas: Optional[int] = None
    cuotas_pagadas: Optional[int] = None


@dataclass
class DeudaResumen:
    """Resultado de get_deudas(): todas las TDC + el/los créditos."""

    tarjetas: list[SaldoTarjeta] = field(default_factory=list)
    creditos: list[SaldoHipotecario] = field(default_factory=list)


@dataclass
class EstadoCuenta:
    """Estado de cuenta facturado de una tarjeta en un período."""

    periodo: date
    tarjeta_id: str
    movimientos: list[MovimientoNormalizado] = field(default_factory=list)
    total_facturado_clp: Decimal = Decimal("0")
    total_facturado_usd: Optional[Decimal] = None
    pago_minimo_clp: Optional[Decimal] = None
    fecha_pago: Optional[date] = None
    tipo_cambio_facturacion: Optional[Decimal] = None


# --- fuentes de activos (inversiones / cripto / previsión) --------------------------
# A diferencia de los bancos (deuda/movimientos de crédito), estas fuentes reportan
# POSICIONES: cuánto tienes de cada instrumento y cuánto vale. Alimentan
# patrimonio.Activo / HistorialActivo, no MovimientoBancario.


@dataclass
class PosicionNormalizada:
    """
    Una tenencia en una fuente de activos: acciones, fondos, cripto, saldo previsional.

    `cantidad` × `precio_unitario` ≈ valor de mercado. Cuando la fuente ya entrega el
    valor total (p.ej. saldo de un fondo mutuo o de una AFP), deja `cantidad`/
    `precio_unitario` en None y llena `valor_clp`/`valor_usd` directamente.
    """

    simbolo: str  # ticker/moneda: "BTC", "AAPL", "Risky Norris"; identificador de la tenencia
    nombre: str  # nombre legible de la tenencia
    tipo_activo: str  # mapea a patrimonio.Activo.TIPO_ACTIVO: CRIPTO/FONDO_MUTUO/ACCIONES/AHORRO/...
    fuente: str  # connector de origen, p.ej. "binance", "fintual"
    cantidad: Optional[Decimal] = None  # unidades (0.5 BTC, 1200 cuotas)
    precio_unitario: Optional[Decimal] = None  # precio por unidad en `moneda`
    moneda: str = "CLP"  # moneda de valorización ("CLP" | "USD")
    valor_clp: Optional[Decimal] = None  # valor de mercado total en CLP
    valor_usd: Optional[Decimal] = None  # valor de mercado total en USD
    horizonte: Optional[str] = None  # patrimonio.Activo.HORIZONTE_CHOICES (opcional)
    es_liquido: bool = True

    def clave(self) -> str:
        """Identidad estable de la posición para upsert (fuente + símbolo)."""
        return f"{self.fuente}:{_norm(self.simbolo)}"


@dataclass
class SaldoEfectivo:
    """Saldo de caja/efectivo disponible en una fuente (ej. CLP no invertido en un broker)."""

    moneda: str  # "CLP" | "USD"
    monto: Decimal = Decimal("0")
    fuente: str = ""
