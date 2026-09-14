from __future__ import annotations

import requests

from configuracion.services.integrations import get_tokenomist_api_key


class TokenomistProviderError(ValueError):
    pass


class TokenomistProvider:
    """Optional token supply/unlock data used to enrich a research packet."""

    base_url = "https://api.tokenomist.ai/v4"

    def __init__(self, session=None, api_key=None):
        self.session = session or requests.Session()
        self.api_key = api_key if api_key is not None else get_tokenomist_api_key()

    def _get(self, path, params=None):
        if not self.api_key:
            return {}
        response = self.session.get(
            f"{self.base_url}{path}",
            params=params or {},
            headers={"x-api-key": self.api_key, "Accept": "application/json"},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json() or {}
        if payload.get("status") is False:
            raise TokenomistProviderError("Tokenomist rechazó la consulta")
        return payload

    def find_token(self, symbol, name=""):
        rows = self._get("/token/list").get("data", []) or []
        symbol = (symbol or "").upper()
        name = (name or "").lower()
        return next((row for row in rows if str(row.get("symbol", "")).upper() == symbol), None) or next((row for row in rows if str(row.get("name", "")).lower() == name), None)

    def fetch_tokenomics(self, symbol, name=""):
        if not self.api_key:
            return None
        token = self.find_token(symbol, name)
        if not token:
            return None
        token_id = token.get("id")
        result = dict(token)
        if token_id:
            events = self._get("/unlock/events", {"tokenId": token_id}).get("data", []) or []
            result["upcoming_unlock_events"] = events[:24]
        return result
