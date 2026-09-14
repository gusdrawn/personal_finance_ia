from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import requests
from django.conf import settings

from .base import AssetCandidate, BarObservation, ProviderCapabilities


class ProviderError(RuntimeError):
    """Error normalizado de un proveedor externo; nunca se expone su secreto."""


class TwelveDataProvider:
    name = "TWELVE_DATA"
    base_url = "https://api.twelvedata.com"

    def __init__(self, api_key: str | None = None, session=None, timeout: int = 15):
        self.api_key = api_key or getattr(settings, "TRADING_TWELVE_DATA_API_KEY", "")
        self.session = session or requests.Session()
        self.timeout = timeout

    def capabilities(self):
        enabled = bool(self.api_key)
        return ProviderCapabilities(catalog=enabled, daily_bars=enabled, fundamentals=enabled, analysts=False, events=False, news=False)

    def _get(self, endpoint: str, **params: Any) -> dict[str, Any]:
        if not self.api_key:
            raise ProviderError("TRADING_TWELVE_DATA_API_KEY no configurada")
        params["apikey"] = self.api_key
        try:
            response = self.session.get(f"{self.base_url}/{endpoint}", params=params, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ProviderError(f"Twelve Data no respondió correctamente: {exc}") from exc
        if payload.get("status") == "error" or payload.get("code", 0) not in (0, None):
            raise ProviderError(str(payload.get("message", "Respuesta inválida de Twelve Data")))
        return payload

    def search_instruments(self, query: str):
        payload = self._get("symbol_search", symbol=query)
        return [AssetCandidate(symbol=item.get("symbol", ""), name=item.get("instrument_name", item.get("symbol", "")), exchange=item.get("exchange", ""), currency=item.get("currency", "USD"), asset_type=_asset_type(item.get("instrument_type"))) for item in payload.get("data", [])]

    def fetch_daily_bars(self, symbol: str, start: date | None = None, end: date | None = None):
        params = {"symbol": symbol, "interval": "1day", "outputsize": 5000, "order": "asc"}
        if start:
            params["start_date"] = start.isoformat()
        if end:
            params["end_date"] = end.isoformat()
        payload = self._get("time_series", **params)
        result = []
        for row in payload.get("values", []):
            result.append(BarObservation(session_date=datetime.strptime(row["datetime"], "%Y-%m-%d").date(), open=Decimal(row["open"]), high=Decimal(row["high"]), low=Decimal(row["low"]), close=Decimal(row["close"]), adjusted_close=Decimal(row.get("adjusted_close", row["close"])), volume=Decimal(row["volume"]) if row.get("volume") else None))
        return result

    def fetch_metrics(self, symbol: str):
        quote = self._get("quote", symbol=symbol)
        return {"price": quote.get("close") or quote.get("price"), "change_pct": quote.get("percent_change"), "volume": quote.get("volume"), "high_52w": quote.get("fifty_two_week", {}).get("high") if isinstance(quote.get("fifty_two_week"), dict) else None}

    def fetch_financial_statements(self, symbol: str):
        return self._get("income_statement", symbol=symbol, period="annual").get("income_statement", [])

    def fetch_analyst_data(self, symbol: str):
        return {}

    def fetch_events(self, start: date, end: date):
        return []

    def fetch_news(self, symbol: str):
        return []


def _asset_type(instrument_type: str | None) -> str:
    value = (instrument_type or "").lower()
    if "etf" in value:
        return "ETF"
    if "forex" in value or "currency" in value:
        return "FOREX"
    if "crypto" in value:
        return "CRYPTO"
    if "index" in value:
        return "INDEX"
    return "STOCK"
