from __future__ import annotations

import requests

from .twelve_data import ProviderError


class SECProvider:
    """Estados financieros públicos y oficiales de SEC EDGAR."""

    name = "SEC_EDGAR"
    tickers_url = "https://www.sec.gov/files/company_tickers.json"
    facts_url = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

    def __init__(self, session=None, timeout=20, user_agent="FinanzasPersonalesIA admin@finance.gustavoguillen.com"):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.user_agent = user_agent
        self._ticker_map = None

    def _get(self, url):
        try:
            response = self.session.get(
                url,
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"},
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ProviderError(f"SEC EDGAR no respondió correctamente: {exc}") from exc

    def resolve_company(self, symbol):
        if self._ticker_map is None:
            payload = self._get(self.tickers_url)
            self._ticker_map = {
                str(item.get("ticker", "")).upper(): item
                for item in payload.values()
                if item.get("ticker") and item.get("cik_str")
            }
        item = self._ticker_map.get(symbol.strip().upper().replace("-", "."))
        if not item:
            raise ProviderError(f"SEC EDGAR no encontró un CIK para {symbol}")
        return {"cik": str(item["cik_str"]).zfill(10), "name": item.get("title", symbol)}

    def fetch_company_facts(self, symbol):
        company = self.resolve_company(symbol)
        payload = self._get(self.facts_url.format(cik=company["cik"]))
        payload["resolved_company"] = company
        return payload
