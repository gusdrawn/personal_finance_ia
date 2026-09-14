from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import requests

from configuracion.services.integrations import get_coingecko_api_key
from .base import AssetCandidate, MarketObservation


class CoinGeckoProvider:
    base_url = "https://api.coingecko.com/api/v3"

    def __init__(self, session=None):
        self.session = session or requests.Session()

    def _get(self, path, params):
        headers = {}
        api_key = get_coingecko_api_key()
        if api_key:
            headers["x-cg-demo-api-key"] = api_key
        response = self.session.get(f"{self.base_url}{path}", params=params, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json()

    def fetch_universe(self, limit=100):
        rows = self._get("/coins/markets", {"vs_currency": "usd", "order": "market_cap_desc", "per_page": min(limit, 250), "page": 1, "sparkline": "false"})
        return [AssetCandidate(str(row["id"]), row["name"], row["symbol"], row.get("market_cap_rank"), Decimal(str(row["market_cap"])) if row.get("market_cap") is not None else None, row.get("links", {}).get("homepage", [""])[0] if row.get("links") else "", row) for row in rows]

    def search(self, query, limit=20):
        """Searches CoinGecko's catalog without ordering by market cap."""
        query = (query or "").strip()
        if not query:
            return []
        payload = self._get("/search", {"query": query})
        rows = (payload or {}).get("coins", [])[: max(1, min(int(limit), 50))]
        return [
            AssetCandidate(
                str(row.get("id", "")),
                row.get("name", ""),
                row.get("symbol", ""),
                row.get("market_cap_rank"),
                None,
                "",
                row,
            )
            for row in rows
            if row.get("id") and row.get("name") and row.get("symbol")
        ]

    def fetch_details(self, external_id):
        """Fetches public project metadata, links and tokenomics for a dossier."""
        return self._get(
            f"/coins/{external_id}",
            {
                "localization": "false",
                "tickers": "false",
                "market_data": "true",
                "community_data": "true",
                "developer_data": "true",
                "sparkline": "false",
            },
        )

    def fetch_market(self, external_ids):
        rows = self._get("/coins/markets", {"vs_currency": "usd", "ids": ",".join(external_ids), "per_page": min(len(external_ids), 250), "page": 1, "sparkline": "false"})
        observations = []
        for row in rows:
            observations.append(MarketObservation(
                external_id=str(row["id"]), observed_at=datetime.now().astimezone(),
                price_usd=Decimal(str(row["current_price"])) if row.get("current_price") is not None else None,
                market_cap_usd=Decimal(str(row["market_cap"])) if row.get("market_cap") is not None else None,
                fdv_usd=Decimal(str(row["fully_diluted_valuation"])) if row.get("fully_diluted_valuation") is not None else None,
                volume_24h_usd=Decimal(str(row["total_volume"])) if row.get("total_volume") is not None else None,
                circulating_supply=Decimal(str(row["circulating_supply"])) if row.get("circulating_supply") is not None else None,
                total_supply=Decimal(str(row["total_supply"])) if row.get("total_supply") is not None else None,
                max_supply=Decimal(str(row["max_supply"])) if row.get("max_supply") is not None else None,
                ath_usd=Decimal(str(row["ath"])) if row.get("ath") is not None else None,
                market_cap_rank=row.get("market_cap_rank"), raw=row,
            ))
        return observations
