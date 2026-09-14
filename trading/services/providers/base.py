from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderCapabilities:
    catalog: bool = False
    daily_bars: bool = False
    fundamentals: bool = False
    analysts: bool = False
    events: bool = False
    news: bool = False


@dataclass(frozen=True)
class AssetCandidate:
    symbol: str
    name: str
    asset_type: str = "STOCK"
    exchange: str = ""
    currency: str = "USD"
    sector: str = ""
    industry: str = ""
    external_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BarObservation:
    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adjusted_close: Decimal | None = None
    volume: Decimal | None = None


class MarketDataProvider(Protocol):
    name: str

    def capabilities(self) -> ProviderCapabilities: ...
    def search_instruments(self, query: str) -> list[AssetCandidate]: ...
    def fetch_daily_bars(self, symbol: str, start: date | None = None, end: date | None = None) -> list[BarObservation]: ...
    def fetch_metrics(self, symbol: str) -> dict[str, Any]: ...
    def fetch_financial_statements(self, symbol: str) -> list[dict[str, Any]]: ...
    def fetch_analyst_data(self, symbol: str) -> dict[str, Any]: ...
    def fetch_events(self, start: date, end: date) -> list[dict[str, Any]]: ...
    def fetch_news(self, symbol: str) -> list[dict[str, Any]]: ...
