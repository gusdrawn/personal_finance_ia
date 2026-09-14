from __future__ import annotations

import requests

from configuracion.models import CredencialIntegracion
from configuracion.services.integrations import get_integration_api_key


class WebSearchProviderError(ValueError):
    pass


class BraveSearchProvider:
    """Optional public-web discovery through Brave Search's JSON API."""

    base_url = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, session=None, api_key=None):
        self.session = session or requests.Session()
        self.api_key = api_key if api_key is not None else get_integration_api_key(CredencialIntegracion.PROVIDER_BRAVE_SEARCH)

    def search(self, query, limit=10):
        query = (query or "").strip()
        if not query:
            return []
        if not self.api_key:
            raise WebSearchProviderError("Búsqueda web no configurada. Agrega la API key de Brave en Configuración.")
        response = self.session.get(
            self.base_url,
            params={"q": query, "count": max(1, min(int(limit), 20)), "safesearch": "strict"},
            headers={"Accept": "application/json", "X-Subscription-Token": self.api_key},
            timeout=15,
        )
        response.raise_for_status()
        results = ((response.json() or {}).get("web", {}) or {}).get("results", [])
        candidates = []
        for item in results[: max(1, min(int(limit), 20))]:
            url = (item.get("url") or "").strip()
            title = (item.get("title") or "").strip()
            if not url or not title:
                continue
            candidates.append(
                {
                    "external_id": url,
                    "name": title[:160],
                    "symbol": "",
                    "source_url": url,
                    "raw": {"description": item.get("description", ""), "profile": item.get("profile", {})},
                }
            )
        return candidates
