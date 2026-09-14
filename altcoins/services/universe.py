from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from altcoins.constants import DEFAULT_THEMES
from altcoins.models import AltcoinExternalId, AltcoinProject, AltcoinTheme
from trading.models import Instrument


EXCLUDED_SYMBOLS = {"btc", "usdt", "usdc", "dai", "usde", "fdusd", "tusd", "usdd", "usdp", "pyusd", "frax", "lusd", "crvusd", "susd", "eurc", "wbtc", "weth"}


def _eligible(candidate):
    if candidate.symbol.lower() in EXCLUDED_SYMBOLS:
        return False
    categories = " ".join(str(item) for item in candidate.raw.get("categories", [])) if candidate.raw else ""
    haystack = f"{candidate.name} {candidate.symbol} {categories}".lower()
    return not any(token in haystack for token in ("stablecoin", "wrapped token", "leveraged token", "3x long", "3x short", "2x long", "2x short"))


def ensure_default_themes():
    for name, slug in DEFAULT_THEMES:
        AltcoinTheme.objects.get_or_create(name=name, slug=slug)


@transaction.atomic
def bootstrap_universe(candidates, limit=45):
    """Crea una membresía estable: solo incorpora los primeros proyectos elegibles."""
    ensure_default_themes()
    existing_count = AltcoinProject.objects.filter(in_universe=True).count()
    next_position = (AltcoinProject.objects.filter(in_universe=True).order_by("-universe_position").values_list("universe_position", flat=True).first() or 0) + 1
    selected = []
    for candidate in candidates:
        if len(selected) >= limit:
            break
        if not _eligible(candidate) or not candidate.market_cap_usd:
            continue
        project, _ = AltcoinProject.objects.get_or_create(
            coingecko_id=candidate.external_id,
            defaults={"name": candidate.name, "symbol": candidate.symbol.upper(), "slug": candidate.external_id[:180], "website": candidate.website},
        )
        # El proyecto cripto comparte la ficha genérica del Research Center.
        if project.instrument_id is None:
            project.instrument, _ = Instrument.objects.get_or_create(
                symbol=f"{project.symbol.upper()}USD",
                exchange="CRYPTO",
                defaults={"name": project.name, "asset_type": "CRYPTO", "currency": "USD", "source": "COINGECKO"},
            )
            project.save(update_fields=["instrument", "updated_at"])
        if not project.in_universe and existing_count < limit:
            project.in_universe = True
            project.universe_position = next_position
            project.universe_added_at = timezone.now()
            project.save(update_fields=["in_universe", "universe_position", "universe_added_at", "updated_at"])
            existing_count += 1
            next_position += 1
        AltcoinExternalId.objects.update_or_create(provider="COINGECKO", external_id=candidate.external_id, defaults={"project": project, "verified": True})
        if project.in_universe:
            selected.append(project)
    return selected[:limit]
