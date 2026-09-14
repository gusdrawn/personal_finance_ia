from __future__ import annotations

from decimal import Decimal

import requests

from configuracion.services.integrations import get_coinmarketcap_api_key
from .base import AssetCandidate


class CoinMarketCapProvider:
    base_url = "https://pro-api.coinmarketcap.com/v1"

    def __init__(self, session=None, api_key=None):
        self.session = session or requests.Session()
        self.api_key = api_key

    def fetch_universe(self, limit=100):
        api_key = self.api_key or get_coinmarketcap_api_key()
        if not api_key:
            return []
        response = self.session.get(
            f"{self.base_url}/cryptocurrency/listings/latest",
            params={"start": 1, "limit": min(limit, 5000), "convert": "USD"},
            headers={"X-CMC_PRO_API_KEY": api_key}, timeout=15,
        )
        response.raise_for_status()
        rows = response.json().get("data", [])
        return [AssetCandidate(str(row["id"]), row["name"], row["symbol"], row.get("cmc_rank"), Decimal(str(row["quote"]["USD"]["market_cap"])) if row.get("quote", {}).get("USD", {}).get("market_cap") is not None else None, raw=row) for row in rows]

    def fetch_market(self, external_ids):
        # El mercado canónico v1 usa CoinGecko; CMC se reserva para metadata y validación.
        return []
