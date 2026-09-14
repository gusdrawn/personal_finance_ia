from __future__ import annotations

from altcoins.models import ProjectEntityRelationship
from .scoring import INSTITUTIONAL_AXES, calculate_institutional_score


def assess_project_institutional_footprint(project):
    """Evalúa relaciones verificadas; candidatos y fuentes débiles quedan fuera."""
    relationships = project.institutional_relationships.filter(status="VERIFIED").prefetch_related("evidence")
    statuses = {}
    statuses["ACCOUNTABILITY"] = "PASS" if relationships.filter(relationship_type__in=["LEGAL_OPERATOR", "FOUNDER", "CORE_TEAM"]).exists() else "UNKNOWN"
    funding = relationships.filter(relationship_type__in=["EQUITY_INVESTOR", "TOKEN_INVESTOR", "LEAD_INVESTOR", "GRANT_PROVIDER", "GOVERNMENT_GRANT"])
    statuses["FUNDING_TRANSPARENCY"] = "PASS" if funding.filter(amount_usd__isnull=False, announced_at__isnull=False).exists() else "PARTIAL" if funding.exists() else "UNKNOWN"
    statuses["CORROBORATION"] = "PASS" if any(rel.evidence.filter(reliability_tier="B_CORROBORATED").exists() for rel in relationships) else "PARTIAL" if any(rel.evidence.filter(reliability_tier="A_PRIMARY").exists() for rel in relationships) else "UNKNOWN"
    statuses["INSTITUTIONAL_FOOTPRINT"] = "PASS" if relationships.filter(relationship_type__in=["EQUITY_INVESTOR", "TOKEN_INVESTOR", "LEAD_INVESTOR", "STRATEGIC_BACKER", "GOVERNMENT_GRANT", "GOVERNMENT_CONTRACT", "PUBLIC_SECTOR_PILOT"]).exists() else "UNKNOWN"
    statuses["CONTROL_TRANSPARENCY"] = "PASS" if relationships.filter(ownership_pct__isnull=False).exists() else "PARTIAL" if relationships.exists() else "UNKNOWN"
    result = calculate_institutional_score(statuses)
    result["statuses"] = statuses
    return result
