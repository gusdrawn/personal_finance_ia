from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from altcoins.models import (
    AltcoinCriterionAssessment,
    AltcoinDiscoveryCandidate,
    AltcoinProject,
    AltcoinProjectTheme,
    AltcoinResearchPacket,
    AltcoinTheme,
    EvidenceRecord,
)
from altcoins.services.evidence import fetch_evidence
from altcoins.services.providers.base import AssetCandidate
from altcoins.services.providers.coingecko import CoinGeckoProvider
from altcoins.services.providers.robinhood import RobinhoodAvailabilityProvider
from altcoins.services.providers.tokenomist import TokenomistProvider
from altcoins.services.providers.web_search import BraveSearchProvider
from altcoins.services.universe import EXCLUDED_SYMBOLS, ensure_default_themes
from trading.models import Instrument


class DiscoveryError(ValueError):
    pass


THEME_KEYWORDS = {
    "ia-descentralizada": ("artificial intelligence", "ai", "machine learning", "agent"),
    "computo-gpu": ("computing", "gpu", "compute"),
    "datos-almacenamiento": ("data", "storage", "oracle"),
    "depin-fisico": ("depin", "physical infrastructure"),
    "rwa-tokenizacion": ("real world", "rwa", "tokenized"),
    "pagos-finanzas": ("payment", "finance", "stablecoin"),
    "defi-perpetuos": ("defi", "decentralized finance", "perpetual"),
    "l1-smart-contracts": ("layer 1", "smart contract", "platform"),
    "privacidad": ("privacy", "zero knowledge", "zkp"),
    "identidad": ("identity", "proof of humanity"),
}


def _as_asset(candidate):
    if isinstance(candidate, AssetCandidate):
        return candidate
    return AssetCandidate(
        external_id=str(candidate.get("external_id", "")),
        name=str(candidate.get("name", "")),
        symbol=str(candidate.get("symbol", "")),
        market_cap_rank=candidate.get("market_cap_rank"),
        market_cap_usd=candidate.get("market_cap_usd"),
        website=str(candidate.get("website", "")),
        raw=candidate.get("raw", {}) or {},
    )


def _candidate_allowed(candidate):
    symbol = (candidate.symbol or "").lower().strip()
    if symbol and symbol in EXCLUDED_SYMBOLS:
        return False
    haystack = f"{candidate.name} {candidate.symbol} {candidate.raw.get('categories', '')}".lower()
    return not any(value in haystack for value in ("stablecoin", "wrapped token", "leveraged token"))


@transaction.atomic
def persist_candidates(candidates, source, query="", source_url=""):
    """Upsert discovery rows without adding them to the scanner universe."""
    saved = []
    for raw_candidate in candidates:
        candidate = _as_asset(raw_candidate)
        if not candidate.external_id or not candidate.name or not _candidate_allowed(candidate):
            continue
        defaults = {
            "name": candidate.name[:160],
            "symbol": (candidate.symbol or "").upper()[:32],
            "coingecko_id": candidate.external_id if source == "COINGECKO_SEARCH" else str(candidate.raw.get("coingecko_id", ""))[:160],
            "website": candidate.website[:1000] if candidate.website else "",
            "source_url": str(candidate.raw.get("source_url", source_url))[:1000],
            "search_query": query[:220],
            "market_cap_rank": candidate.market_cap_rank,
            "market_cap_usd": candidate.market_cap_usd,
            "raw_payload": candidate.raw or {},
        }
        item, _created = AltcoinDiscoveryCandidate.objects.update_or_create(
            source=source,
            external_id=candidate.external_id[:180],
            defaults=defaults,
        )
        if item.status in {"ERROR", "REJECTED"}:
            item.status = "NEW"
            item.last_error = ""
            item.save(update_fields=["status", "last_error", "last_seen_at"])
        saved.append(item)
    return saved


def discover_by_query(query, source="COINGECKO_SEARCH", limit=20, coingecko_provider=None, web_provider=None):
    query = (query or "").strip()
    if not query:
        raise DiscoveryError("Escribe un proyecto, ticker o tema para descubrir candidatos.")
    if source == "COINGECKO_SEARCH":
        candidates = (coingecko_provider or CoinGeckoProvider()).search(query, limit=limit)
        return persist_candidates(candidates, source, query=query, source_url="https://www.coingecko.com/")
    if source == "WEB_SEARCH":
        candidates = (web_provider or BraveSearchProvider()).search(query, limit=limit)
        return persist_candidates(candidates, source, query=query)
    raise DiscoveryError("Fuente de descubrimiento no reconocida.")


def discover_robinhood(limit=250, provider=None):
    provider = provider or RobinhoodAvailabilityProvider()
    candidates = provider.fetch_catalog(limit=limit)
    return persist_candidates(
        candidates,
        "ROBINHOOD_PUBLIC",
        source_url=getattr(provider, "source_url", RobinhoodAvailabilityProvider.source_url),
    )


def _unique_slug(name, symbol, pk=None):
    base = slugify(f"{name}-{symbol}")[:170] or f"altcoin-{pk or 'candidate'}"
    slug = base
    suffix = 1
    while AltcoinProject.objects.filter(slug=slug).exists():
        slug = f"{base[:170 - len(str(suffix)) - 1]}-{suffix}"
        suffix += 1
    return slug


@transaction.atomic
def promote_candidate(candidate):
    """Promotes a researched candidate, without replacing the legacy top-45 set."""
    if candidate.promoted_project_id:
        return candidate.promoted_project
    if not (candidate.symbol or "").strip():
        raise DiscoveryError("El candidato no tiene ticker. Investígalo primero para resolver su identidad.")
    project = None
    if candidate.coingecko_id:
        project = AltcoinProject.objects.filter(coingecko_id=candidate.coingecko_id).first()
    if project is None:
        project = AltcoinProject.objects.filter(name__iexact=candidate.name, symbol__iexact=candidate.symbol).first()
    if project is None:
        project = AltcoinProject.objects.create(
            name=candidate.name,
            symbol=candidate.symbol.upper() or "N/A",
            slug=_unique_slug(candidate.name, candidate.symbol, candidate.pk),
            website=candidate.website,
            coingecko_id=candidate.coingecko_id or None,
        )
    max_position = AltcoinProject.objects.filter(in_universe=True).order_by("-universe_position").values_list("universe_position", flat=True).first() or 0
    project.active = True
    project.in_universe = True
    if not project.universe_position:
        project.universe_position = max_position + 1
        project.universe_added_at = timezone.now()
    if candidate.website and not project.website:
        project.website = candidate.website
    project.save(update_fields=["active", "in_universe", "universe_position", "universe_added_at", "website", "updated_at"])
    if project.instrument_id is None:
        project.instrument, _ = Instrument.objects.get_or_create(
            symbol=f"{project.symbol.upper()}USD",
            exchange="CRYPTO",
            defaults={"name": project.name, "asset_type": "CRYPTO", "currency": "USD", "source": candidate.source},
        )
        project.save(update_fields=["instrument", "updated_at"])
    candidate.promoted_project = project
    candidate.status = "PROMOTED"
    candidate.last_error = ""
    candidate.save(update_fields=["promoted_project", "status", "last_error", "last_seen_at"])
    AltcoinResearchPacket.objects.filter(candidate=candidate, project__isnull=True).update(project=project)
    return project


class _VisibleTextExtractor:
    """Small dependency-free HTML text extractor for bounded evidence excerpts."""

    def __init__(self):
        from html.parser import HTMLParser

        class Parser(HTMLParser):
            def __init__(self, owner):
                super().__init__(convert_charrefs=True)
                self.owner = owner
                self.skip = 0

            def handle_starttag(self, tag, attrs):
                if tag.lower() in {"script", "style", "noscript", "svg"}:
                    self.skip += 1

            def handle_endtag(self, tag):
                if tag.lower() in {"script", "style", "noscript", "svg"} and self.skip:
                    self.skip -= 1

            def handle_data(self, data):
                if not self.skip and data.strip():
                    self.owner.parts.append(data.strip())

        self.parts = []
        self.parser = Parser(self)

    def feed(self, html):
        self.parser.feed(html)
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


def _link_kind(url):
    host = (urlparse(url).hostname or "").lower()
    social_hosts = ("x.com", "twitter.com", "t.me", "telegram.me", "discord.com", "discord.gg", "github.com", "reddit.com", "medium.com", "warpcast.com", "farcaster.xyz")
    return "SOCIAL" if any(host == item or host.endswith(f".{item}") for item in social_hosts) else "PROJECT_OFFICIAL"


def _iter_links(details):
    links = details.get("links", {}) if isinstance(details, dict) else {}
    values = []
    for key in ("homepage", "blockchain_site", "official_forum_url", "announcement_url", "chat_url"):
        values.extend(value for value in links.get(key, []) if value)
    twitter = links.get("twitter_screen_name")
    if twitter:
        values.append(f"https://x.com/{twitter.lstrip('@')}")
    for repo in (links.get("repos_url", {}) or {}).get("github", []) or []:
        values.append(repo)
    if links.get("subreddit_url"):
        values.append(links["subreddit_url"])
    seen = set()
    for value in values:
        value = (value or "").strip()
        if value and value not in seen and value.startswith("https://"):
            seen.add(value)
            yield value


def _str_decimal(value):
    try:
        return str(Decimal(str(value))) if value is not None else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _normalize_details(details):
    details = details or {}
    market = details.get("market_data", {}) or {}
    def md(key):
        value = market.get(key)
        if isinstance(value, dict):
            return value.get("usd")
        return value

    tokenomics = {
        "circulating_supply": _str_decimal(market.get("circulating_supply")),
        "total_supply": _str_decimal(market.get("total_supply")),
        "max_supply": _str_decimal(market.get("max_supply")),
        "market_cap_usd": _str_decimal(md("market_cap")),
        "fdv_usd": _str_decimal(md("fully_diluted_valuation")),
        "fdv_to_market_cap": None,
    }
    try:
        if tokenomics["fdv_usd"] and tokenomics["market_cap_usd"] and Decimal(tokenomics["market_cap_usd"]) > 0:
            tokenomics["fdv_to_market_cap"] = str((Decimal(tokenomics["fdv_usd"]) / Decimal(tokenomics["market_cap_usd"])).quantize(Decimal("0.0001")))
    except (InvalidOperation, TypeError):
        pass
    ath_change = (market.get("ath_change_percentage", {}) or {}).get("usd")
    return {
        "identity": {"name": details.get("name", ""), "symbol": details.get("symbol", ""), "description": (details.get("description", {}).get("en", "") if isinstance(details.get("description"), dict) else "")[:3000]},
        "market": {**{key: _str_decimal(md(key)) for key in ("current_price", "market_cap", "fully_diluted_valuation", "total_volume", "ath")}, "ath_drawdown_pct": _str_decimal(-Decimal(str(ath_change))) if ath_change is not None else None},
        "tokenomics": tokenomics,
        "community": {key: details.get("community_data", {}).get(key) for key in ("twitter_followers", "reddit_subscribers", "telegram_channel_user_count")},
        "developer": {"forks": details.get("developer_data", {}).get("forks"), "stars": details.get("developer_data", {}).get("stars"), "last_4_weeks_commit_count": details.get("developer_data", {}).get("commit_count_4_weeks")},
        "categories": details.get("categories", [])[:20] if isinstance(details.get("categories"), list) else [],
    }


def _apply_deterministic_assessments(project, payload, input_hash):
    tokenomics = payload.get("tokenomics", {})
    market = payload.get("market", {})
    facts = {}
    circ, maximum = tokenomics.get("circulating_supply"), tokenomics.get("max_supply")
    if circ and maximum and Decimal(maximum) > 0:
        ratio = Decimal(circ) / Decimal(maximum)
        facts["SUPPLY"] = ("PASS" if ratio >= Decimal("0.9") else "PARTIAL" if ratio >= Decimal("0.6") else "FAIL", f"Circulante/max supply: {(ratio * 100).quantize(Decimal('0.1'))}%.")
    fdv_ratio = tokenomics.get("fdv_to_market_cap")
    if fdv_ratio:
        ratio = Decimal(fdv_ratio)
        facts["VALUATION"] = ("PASS" if ratio <= Decimal("1.25") else "PARTIAL" if ratio <= Decimal("2") else "FAIL", f"FDV/market cap: {ratio}x.")
    drawdown = market.get("ath_drawdown_pct")
    if drawdown is not None:
        value = Decimal(drawdown)
        facts["ATH_DISCOUNT"] = ("PASS" if value >= 70 else "PARTIAL" if value >= 40 else "FAIL", f"Distancia al ATH: {value}%.")
    for code, (status, rationale) in facts.items():
        AltcoinCriterionAssessment.objects.update_or_create(
            project=project,
            criterion_code=code,
            defaults={"status": status, "confidence": Decimal("90"), "source_mode": "AUTOMATIC", "rationale": rationale, "input_hash": input_hash},
        )


def _attach_themes(project, payload):
    ensure_default_themes()
    categories = [str(value).lower() for value in payload.get("categories", [])]
    for category in categories:
        for slug, keywords in THEME_KEYWORDS.items():
            if any(keyword in category for keyword in keywords):
                theme = AltcoinTheme.objects.filter(slug=slug, active=True).first()
                if theme:
                    AltcoinProjectTheme.objects.update_or_create(
                        project=project,
                        theme=theme,
                        defaults={"confidence": Decimal("75"), "source": "COINGECKO"},
                    )


def build_research_packet(candidate, details_provider=None, tokenomist_provider=None, fetcher=fetch_evidence, max_sources=8):
    """Collects market/tokenomics/official/social inputs into an auditable packet."""
    packet = AltcoinResearchPacket.objects.create(candidate=candidate, project=candidate.promoted_project, status="RUNNING")
    try:
        details = {}
        if candidate.coingecko_id:
            details = (details_provider or CoinGeckoProvider()).fetch_details(candidate.coingecko_id)
        normalized = _normalize_details(details)
        if not normalized["identity"]["name"]:
            normalized["identity"].update({"name": candidate.name, "symbol": candidate.symbol})
        tokenomist = None
        tokenomist_client = tokenomist_provider or TokenomistProvider()
        if candidate.symbol and getattr(tokenomist_client, "api_key", ""):
            try:
                tokenomist = tokenomist_client.fetch_tokenomics(candidate.symbol, candidate.name)
            except Exception as exc:
                tokenomist = {"error": str(exc)[:240]}
        normalized["tokenomics"]["tokenomist"] = tokenomist or {}
        source_entries = []
        if candidate.source_url:
            source_entries.append((candidate.source_url, "ROBINHOOD_PUBLIC" if candidate.source == "ROBINHOOD_PUBLIC" else "WEB_SEARCH"))
        if candidate.coingecko_id:
            source_entries.append((f"https://www.coingecko.com/en/coins/{candidate.coingecko_id}", "COINGECKO"))
        if tokenomist and tokenomist.get("id"):
            source_entries.append((tokenomist.get("websiteUrl") or f"https://tokenomist.ai/{tokenomist['id']}", "TOKENOMIST"))
        source_entries.extend((_url, _link_kind(_url)) for _url in _iter_links(details))
        seen = set()
        sources = []
        for url, source_type in source_entries[: max(1, min(int(max_sources), 12))]:
            if url in seen:
                continue
            seen.add(url)
            source = {"url": url, "kind": source_type, "fetched": False, "evidence_id": None, "excerpt": ""}
            try:
                if source_type == "COINGECKO":
                    raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
                    content_hash = hashlib.sha256(raw).hexdigest()
                    excerpt = normalized["identity"].get("description", "")[:2000]
                else:
                    fetched = fetcher(url)
                    content_hash = fetched["content_hash"]
                    content_type = fetched.get("content_type", "")
                    if content_type == "text/html":
                        excerpt = _VisibleTextExtractor().feed(fetched["body"].decode("utf-8", errors="replace"))[:2000]
                    elif content_type == "application/json":
                        excerpt = fetched["body"].decode("utf-8", errors="replace")[:2000]
                    else:
                        excerpt = "Documento PDF recibido; requiere lectura específica."
                evidence, _created = EvidenceRecord.objects.update_or_create(
                    source_url=url,
                    content_hash=content_hash,
                    defaults={
                        "source_type": source_type,
                        "title": f"{candidate.name} · {source_type}",
                        "publisher": "CoinGecko" if source_type == "COINGECKO" else urlparse(url).hostname or "",
                        "excerpt": excerpt,
                        "reliability_tier": "B_CORROBORATED" if source_type in {"COINGECKO", "ROBINHOOD_PUBLIC"} else "D_UNVERIFIED" if source_type == "SOCIAL" else "A_PRIMARY",
                        "raw_payload": {"candidate_id": candidate.id, "content_type": content_type if source_type != "COINGECKO" else "application/json"},
                        "active": True,
                    },
                )
                packet.evidence.add(evidence)
                source.update({"fetched": True, "evidence_id": evidence.id, "excerpt": excerpt})
            except Exception as exc:  # A single blocked social site must not lose the packet.
                source["error"] = str(exc)[:240]
            sources.append(source)
        normalized.update({"sources": sources, "candidate": {"id": candidate.id, "source": candidate.source, "query": candidate.search_query}})
        input_hash = hashlib.sha256(json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
        packet.payload = normalized
        packet.input_hash = input_hash
        packet.source_count = len(sources)
        packet.evidence_count = packet.evidence.count()
        packet.status = "SUCCESS" if sources and any(item.get("fetched") for item in sources) else "PARTIAL"
        packet.finished_at = timezone.now()
        packet.save(update_fields=["payload", "input_hash", "source_count", "evidence_count", "status", "finished_at"])
        candidate.status = "PROMOTED" if candidate.promoted_project_id else "RESEARCHED"
        candidate.last_error = ""
        candidate.save(update_fields=["status", "last_error", "last_seen_at"])
        if packet.project_id:
            _attach_themes(packet.project, normalized)
            _apply_deterministic_assessments(packet.project, normalized, input_hash)
        return packet
    except Exception as exc:
        packet.status = "FAILED"
        packet.error_message = str(exc)[:2000]
        packet.finished_at = timezone.now()
        packet.save(update_fields=["status", "error_message", "finished_at"])
        candidate.status = "ERROR"
        candidate.last_error = str(exc)[:2000]
        candidate.save(update_fields=["status", "last_error", "last_seen_at"])
        raise DiscoveryError("No se pudo construir el dossier del candidato.") from exc


def research_candidate(candidate, coingecko_provider=None, details_provider=None, tokenomist_provider=None, fetcher=fetch_evidence, max_sources=8):
    """Resolves a catalog-only symbol against CoinGecko before collecting details."""
    if not candidate.coingecko_id:
        provider = coingecko_provider or CoinGeckoProvider()
        query = f"{candidate.name} {candidate.symbol}".strip()
        hits = provider.search(query, limit=10)
        exact = next(
            (
                item for item in hits
                if item.symbol.lower() == candidate.symbol.lower()
                or item.name.lower() == candidate.name.lower()
            ),
            hits[0] if hits else None,
        )
        if exact:
            candidate.coingecko_id = exact.external_id[:160]
            if not candidate.symbol:
                candidate.symbol = exact.symbol.upper()[:32]
            candidate.website = candidate.website or exact.website
            candidate.save(update_fields=["coingecko_id", "symbol", "website", "last_seen_at"])
    return build_research_packet(candidate, details_provider=details_provider, tokenomist_provider=tokenomist_provider, fetcher=fetcher, max_sources=max_sources)
