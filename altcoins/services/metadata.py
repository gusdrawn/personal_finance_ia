from __future__ import annotations

from altcoins.models import AltcoinExternalId, AltcoinProject


def sync_coinmarketcap_metadata(provider, limit=5000):
    """Cruza CMC solo por nombre exacto y único; ticker aislado nunca basta."""
    candidates = provider.fetch_universe(limit=limit)
    matched = 0
    rejected = 0
    for candidate in candidates:
        projects = list(AltcoinProject.objects.filter(name__iexact=candidate.name, in_universe=True))
        if len(projects) != 1:
            rejected += 1
            continue
        project = projects[0]
        project.coinmarketcap_id = int(candidate.external_id)
        project.save(update_fields=["coinmarketcap_id", "updated_at"])
        AltcoinExternalId.objects.update_or_create(provider="CMC", external_id=candidate.external_id, defaults={"project": project, "verified": True})
        matched += 1
    return {"matched": matched, "rejected": rejected, "received": len(candidates)}
