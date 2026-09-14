from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol


@dataclass
class AssetCandidate:
    external_id: str
    name: str
    symbol: str
    market_cap_rank: int | None = None
    market_cap_usd: Decimal | None = None
    website: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketObservation:
    external_id: str
    observed_at: datetime
    price_usd: Decimal | None = None
    market_cap_usd: Decimal | None = None
    fdv_usd: Decimal | None = None
    volume_24h_usd: Decimal | None = None
    circulating_supply: Decimal | None = None
    total_supply: Decimal | None = None
    max_supply: Decimal | None = None
    ath_usd: Decimal | None = None
    ath_date: datetime | None = None
    market_cap_rank: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class MarketProvider(Protocol):
    def fetch_universe(self, limit: int = 100) -> list[AssetCandidate]: ...

    def fetch_market(self, external_ids: list[str]) -> list[MarketObservation]: ...


class DiscoveryProvider(Protocol):
    def search(self, query: str, limit: int = 20) -> list[AssetCandidate]: ...
