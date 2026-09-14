from __future__ import annotations

from datetime import date

import requests
from django.conf import settings

from .twelve_data import ProviderError


class FREDProvider:
    name = "FRED"

    def __init__(self, api_key=None, session=None, timeout=15):
        self.api_key = api_key or getattr(settings, "TRADING_FRED_API_KEY", "")
        self.session = session or requests.Session()
        self.timeout = timeout

    def fetch_series(self, series_id: str, start: date | None = None, end: date | None = None):
        if not self.api_key:
            raise ProviderError("TRADING_FRED_API_KEY no configurada")
        params = {"series_id": series_id, "api_key": self.api_key, "file_type": "json"}
        if start:
            params["observation_start"] = start.isoformat()
        if end:
            params["observation_end"] = end.isoformat()
        response = self.session.get("https://api.stlouisfed.org/fred/series/observations", params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json().get("observations", [])
