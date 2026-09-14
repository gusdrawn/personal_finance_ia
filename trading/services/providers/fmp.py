from __future__ import annotations

from datetime import date

import requests
from django.conf import settings

from .base import AssetCandidate, ProviderCapabilities
from .twelve_data import ProviderError


class FMPProvider:
    """Adaptador para los endpoints ``stable`` de Financial Modeling Prep."""

    name = "FMP"
    base_url = "https://financialmodelingprep.com/stable"

    def __init__(self, api_key=None, session=None, timeout=15):
        self.api_key = api_key or getattr(settings, "TRADING_FMP_API_KEY", "")
        self.session = session or requests.Session()
        self.timeout = timeout

    def capabilities(self):
        enabled = bool(self.api_key)
        return ProviderCapabilities(catalog=enabled, fundamentals=enabled, analysts=enabled, events=enabled, news=enabled)

    def _get(self, endpoint, **params):
        if not self.api_key:
            raise ProviderError("TRADING_FMP_API_KEY no configurada")
        params["apikey"] = self.api_key
        try:
            response = self.session.get(f"{self.base_url}/{endpoint}", params=params, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ProviderError(f"FMP no respondió correctamente: {exc}") from exc
        if isinstance(payload, dict):
            detail = payload.get("Error Message") or payload.get("error") or payload.get("message")
            if detail and not any(key in payload for key in ("symbol", "date", "results")):
                raise ProviderError(str(detail))
        return payload

    def search_instruments(self, query):
        payload = self._get("search-symbol", query=query, limit=25)
        return [
            AssetCandidate(
                symbol=item.get("symbol", ""),
                name=item.get("name", item.get("symbol", "")),
                exchange=item.get("exchangeShortName", item.get("stockExchange", "")),
                currency=item.get("currency", "USD"),
                asset_type="STOCK",
            )
            for item in payload or []
            if item.get("symbol")
        ]

    def fetch_profile(self, symbol):
        return (self._get("profile", symbol=symbol) or [{}])[0]

    def fetch_financial_statements(self, symbol):
        common = {"symbol": symbol, "period": "annual", "limit": 10}
        return {
            "income": self._get("income-statement", **common),
            "balance": self._get("balance-sheet-statement", **common),
            "cashflow": self._get("cash-flow-statement", **common),
        }

    def fetch_analyst_data(self, symbol):
        return {
            "estimates": self._get("analyst-estimates", symbol=symbol, period="annual", page=0, limit=10),
            "grades": self._get("grades-consensus", symbol=symbol),
            "targets": self._get("price-target-consensus", symbol=symbol),
        }

    def fetch_events(self, start: date, end: date):
        return self._get("earnings-calendar", **{"from": start.isoformat(), "to": end.isoformat()})

    def fetch_metrics(self, symbol):
        return (self._get("ratios-ttm", symbol=symbol) or [{}])[0]

    def fetch_daily_bars(self, symbol, start=None, end=None):
        return []

    def fetch_news(self, symbol):
        return self._get("news/stock-latest", symbols=symbol, page=0, limit=50)
