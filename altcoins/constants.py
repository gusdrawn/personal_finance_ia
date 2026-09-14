from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


FUNDAMENTAL_CRITERIA = (
    ("SUPPLY", "Oferta sin dilución"),
    ("FEES", "Flujo real (fees)"),
    ("GROWTH", "Crecimiento"),
    ("VALUE_CAPTURE", "Rentable / captura de valor"),
    ("NARRATIVE", "Narrativa correcta"),
    ("ATH_DISCOUNT", "Descuento vs ATH"),
    ("EXTERNAL_DEMAND", "Demanda externa"),
    ("VALUATION", "Valoración barata"),
)

DEFAULT_THEMES = (
    ("IA descentralizada", "ia-descentralizada"),
    ("Cómputo / GPU", "computo-gpu"),
    ("Datos y almacenamiento", "datos-almacenamiento"),
    ("DePIN físico", "depin-fisico"),
    ("RWA y tokenización", "rwa-tokenizacion"),
    ("Pagos y finanzas", "pagos-finanzas"),
    ("DeFi y perpetuos", "defi-perpetuos"),
    ("L1 / Smart Contracts", "l1-smart-contracts"),
    ("Privacidad", "privacidad"),
    ("Identidad", "identidad"),
    ("Otros", "otros"),
)


@dataclass(frozen=True)
class ScorePolicy:
    """Umbrales versionados para que el modelo pequeño no invente criterios."""

    version: str = "2026-08-v1"
    min_coverage_complete: Decimal = Decimal("75")
    min_coverage_provisional: Decimal = Decimal("50")


POLICY = ScorePolicy()
