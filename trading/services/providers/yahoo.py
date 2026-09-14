from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import requests

from .base import AssetCandidate, BarObservation, ProviderCapabilities
from .twelve_data import ProviderError


class YahooFinanceProvider:
    """Precios e históricos públicos de Yahoo Finance, sin credenciales."""

    name = "YAHOO_FINANCE"
    chart_url = "https://query1.finance.yahoo.com/v8/finance/chart"
    search_url = "https://query2.finance.yahoo.com/v1/finance/search"

    SYMBOL_ALIASES = {
        "BTCUSD": "BTC-USD",
        "ETHUSD": "ETH-USD",
    }

    def __init__(self, session=None, timeout=20):
        self.session = session or requests.Session()
        self.timeout = timeout
        self._cache = {}

    def capabilities(self):
        return ProviderCapabilities(catalog=True, daily_bars=True, fundamentals=False, analysts=False, events=False, news=False)

    def _request_json(self, url, **params):
        try:
            response = self.session.get(
                url,
                params=params,
                timeout=self.timeout,
                headers={"User-Agent": "Mozilla/5.0 FinanzasPersonalesIA/1.0"},
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ProviderError(f"Yahoo Finance no respondió correctamente: {exc}") from exc
        return payload

    def _provider_symbol(self, symbol):
        normalized = symbol.strip().upper()
        if normalized in self.SYMBOL_ALIASES:
            return self.SYMBOL_ALIASES[normalized]
        if normalized.endswith("USD") and len(normalized) > 6:
            return f"{normalized[:-3]}-USD"
        return normalized

    def _chart(self, symbol):
        provider_symbol = self._provider_symbol(symbol)
        if provider_symbol not in self._cache:
            payload = self._request_json(
                f"{self.chart_url}/{provider_symbol}",
                range="5y",
                interval="1d",
                events="div,splits",
                includeAdjustedClose="true",
            )
            error = (payload.get("chart") or {}).get("error")
            result = ((payload.get("chart") or {}).get("result") or [None])[0]
            if error or not result:
                description = (error or {}).get("description") if isinstance(error, dict) else None
                raise ProviderError(description or f"Yahoo Finance no encontró {symbol}")
            self._cache[provider_symbol] = result
        return self._cache[provider_symbol]

    def search_instruments(self, query):
        payload = self._request_json(self.search_url, q=query, quotesCount=15, newsCount=0)
        results = []
        for item in payload.get("quotes", []):
            quote_type = (item.get("quoteType") or "").upper()
            if quote_type not in {"EQUITY", "ETF", "CRYPTOCURRENCY", "INDEX", "CURRENCY", "FUTURE"}:
                continue
            asset_type = {
                "EQUITY": "STOCK",
                "ETF": "ETF",
                "CRYPTOCURRENCY": "CRYPTO",
                "INDEX": "INDEX",
                "CURRENCY": "FOREX",
                "FUTURE": "FUTURE",
            }.get(quote_type, "STOCK")
            results.append(
                AssetCandidate(
                    symbol=item.get("symbol", ""),
                    name=item.get("longname") or item.get("shortname") or item.get("symbol", ""),
                    exchange=item.get("exchDisp") or item.get("exchange", ""),
                    currency=item.get("currency", "USD"),
                    asset_type=asset_type,
                    sector=item.get("sector", ""),
                    industry=item.get("industry", ""),
                )
            )
        return [item for item in results if item.symbol]

    def fetch_daily_bars(self, symbol, start=None, end=None):
        result = self._chart(symbol)
        timestamps = result.get("timestamp") or []
        quote = (((result.get("indicators") or {}).get("quote") or [{}])[0])
        adjusted = (((result.get("indicators") or {}).get("adjclose") or [{}])[0]).get("adjclose") or []
        bars = []
        for index, timestamp in enumerate(timestamps):
            try:
                session_date = datetime.utcfromtimestamp(timestamp).date()
                if start and session_date < start:
                    continue
                if end and session_date > end:
                    continue
                values = {key: (quote.get(key) or [])[index] for key in ("open", "high", "low", "close")}
                if any(value is None for value in values.values()):
                    continue
                bars.append(
                    BarObservation(
                        session_date=session_date,
                        open=Decimal(str(values["open"])),
                        high=Decimal(str(values["high"])),
                        low=Decimal(str(values["low"])),
                        close=Decimal(str(values["close"])),
                        adjusted_close=Decimal(str(adjusted[index])) if index < len(adjusted) and adjusted[index] is not None else Decimal(str(values["close"])),
                        volume=Decimal(str((quote.get("volume") or [])[index])) if index < len(quote.get("volume") or []) and (quote.get("volume") or [])[index] is not None else None,
                    )
                )
            except (IndexError, TypeError, ValueError):
                continue
        return bars

    def fetch_metrics(self, symbol):
        meta = self._chart(symbol).get("meta") or {}
        market_time = meta.get("regularMarketTime")
        return {
            "price": meta.get("regularMarketPrice"),
            "change_pct": meta.get("regularMarketChangePercent"),
            "previous_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
            "volume": meta.get("regularMarketVolume"),
            "high_52w": meta.get("fiftyTwoWeekHigh"),
            "low_52w": meta.get("fiftyTwoWeekLow"),
            "market_time": datetime.fromtimestamp(market_time, tz=timezone.utc).isoformat() if market_time else None,
            "exchange_timezone": meta.get("exchangeTimezoneName"),
            "currency": meta.get("currency"),
            "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
            "long_name": meta.get("longName") or meta.get("shortName"),
            "source_url": f"https://finance.yahoo.com/quote/{self._provider_symbol(symbol)}",
        }
