from __future__ import annotations

import re
from html.parser import HTMLParser

import requests

from altcoins.services.evidence import fetch_evidence
from .base import AssetCandidate


class RobinhoodAvailabilityProvider:
    """Reads Robinhood's public availability catalog.

    The catalog is deliberately treated as a discovery source, not as proof that
    an asset can be traded in every jurisdiction. Robinhood's own table contains
    separate market-data and trading flags; those flags are retained in the raw
    payload for human review.
    """

    source_url = "https://robinhood.com/us/en/support/articles/crypto-availability/"

    def __init__(self, session=None):
        self.session = session or requests.Session()

    @staticmethod
    def parse_catalog(html: str, source_url: str | None = None) -> list[AssetCandidate]:
        source_url = source_url or RobinhoodAvailabilityProvider.source_url
        # The public table is rendered as text in addition to links. This parser
        # intentionally captures only the conservative "Name (TICKER)" shape.
        class VisibleText(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.parts = []
                self.cells = []
                self.current_cell = None
                self.skip = 0

            def handle_starttag(self, tag, attrs):
                if tag.lower() in {"script", "style", "noscript", "svg"}:
                    self.skip += 1
                if tag.lower() in {"td", "th"}:
                    self.current_cell = []

            def handle_endtag(self, tag):
                if tag.lower() in {"script", "style", "noscript", "svg"} and self.skip:
                    self.skip -= 1
                if tag.lower() in {"td", "th"} and self.current_cell is not None:
                    self.cells.append(re.sub(r"\s+", " ", " ".join(self.current_cell)).strip())
                    self.current_cell = None

            def handle_data(self, data):
                if not self.skip and data.strip():
                    self.parts.append(data.strip())
                    if self.current_cell is not None:
                        self.current_cell.append(data.strip())

        parser = VisibleText()
        parser.feed(html or "")
        visible = re.sub(r"\s+", " ", " ".join(parser.parts))
        pattern = r"([A-Za-z0-9][A-Za-z0-9 .&'\-/]{1,90}?)\s*\(([A-Z0-9][A-Z0-9.\-]{1,11})\)"
        if parser.cells:
            matches = []
            for chunk in parser.cells:
                match = re.search(rf"^\s*{pattern}", chunk)
                if match:
                    matches.append(match.groups())
        else:
            matches = re.findall(pattern, visible)
        seen = set()
        candidates = []
        for name, symbol in matches:
            symbol = symbol.upper()
            key = (name.strip().lower(), symbol)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                AssetCandidate(
                    external_id=f"robinhood:{symbol.lower()}",
                    name=name.strip(),
                    symbol=symbol,
                    raw={
                        "source_url": source_url,
                        "catalog": "public_availability",
                        "jurisdiction_note": "La disponibilidad de datos y trading depende de la jurisdicción.",
                    },
                )
            )
        return candidates

    def fetch_catalog(self, limit=250) -> list[AssetCandidate]:
        payload = fetch_evidence(self.source_url, session=self.session)
        html = payload["body"].decode("utf-8", errors="replace")
        return self.parse_catalog(html, payload["url"])[: max(1, min(int(limit), 500))]
