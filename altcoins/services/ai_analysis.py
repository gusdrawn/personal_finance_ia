from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from django.utils import timezone

from banca.services.clasificacion_ia import _clasificador_usuario

from altcoins.models import AltcoinAIAnalysis, AltcoinCriterionOverride
from altcoins.services.institutional_scoring import assess_project_institutional_footprint
from altcoins.services.scoring import FUNDAMENTAL_CRITERIA, calculate_fundamental_score, trap_signals


PROMPT_VERSION = "2026-08-v1"


class AltcoinAIError(ValueError):
    pass


def _payload(project, user):
    assessments = {item.criterion_code: item.status for item in project.criterion_assessments.all()}
    overrides = {item.criterion_code: item.status for item in AltcoinCriterionOverride.objects.filter(user=user, project=project)}
    statuses = {code: overrides.get(code, assessments.get(code, "UNKNOWN")) for code, _label in FUNDAMENTAL_CRITERIA}
    relationships = []
    evidence_ids = set()
    evidence = []
    for relation in project.institutional_relationships.filter(status="VERIFIED").select_related("entity").prefetch_related("evidence"):
        ids = list(relation.evidence.values_list("id", flat=True))
        evidence_ids.update(ids)
        for item in relation.evidence.all():
            evidence.append({"id": item.id, "title": item.title, "tier": item.reliability_tier, "url": item.source_url, "excerpt": item.excerpt})
        relationships.append({"entity_id": relation.entity_id, "entity": relation.entity.canonical_name, "type": relation.relationship_type, "valid_to": relation.valid_to.isoformat() if relation.valid_to else None, "evidence_ids": ids})
    market = project.market_snapshots.order_by("-observed_at").first()
    return {
        "project": {"id": project.id, "name": project.name, "symbol": project.symbol, "website": project.website},
        "market": {"price_usd": str(market.price_usd) if market and market.price_usd is not None else None, "market_cap_usd": str(market.market_cap_usd) if market and market.market_cap_usd is not None else None, "fdv_usd": str(market.fdv_usd) if market and market.fdv_usd is not None else None, "ath_drawdown_pct": str(market.ath_drawdown_pct) if market and market.ath_drawdown_pct is not None else None},
        "criteria": statuses,
        "overrides": overrides,
        "fundamental_score": calculate_fundamental_score(statuses),
        "institutional_score": assess_project_institutional_footprint(project),
        "risk_signals": trap_signals(statuses),
        "verified_relationships": relationships,
        "evidence": evidence,
        "allowed_evidence_ids": sorted(evidence_ids),
    }


def _validate_result(result, allowed_evidence_ids, allowed_entity_ids):
    if not isinstance(result, dict):
        raise AltcoinAIError("La IA no devolvió un objeto JSON")
    for key, default in (("summary", ""), ("thesis", []), ("fundamental_strengths", []), ("fundamental_risks", []), ("institutional_summary", ""), ("supporting_entities", []), ("conflicts", []), ("unknowns", []), ("risk_signals", []), ("confidence", 0)):
        result.setdefault(key, default)
    if not isinstance(result["supporting_entities"], list):
        raise AltcoinAIError("supporting_entities debe ser una lista")
    for item in result["supporting_entities"]:
        if not isinstance(item, dict):
            raise AltcoinAIError("Cada entidad respaldante debe ser un objeto")
        if item.get("entity_id") not in allowed_entity_ids:
            raise AltcoinAIError("La IA referenció una entidad no autorizada")
        evidence_ids = item.get("evidence_ids", [])
        if not evidence_ids or any(evidence_id not in allowed_evidence_ids for evidence_id in evidence_ids):
            raise AltcoinAIError("La IA referenció una evidencia no autorizada")
    return result


class DefaultAltcoinAnalyst:
    def analyze(self, payload, user):
        import requests

        classifier = _clasificador_usuario(user)
        if not classifier.disponible():
            raise AltcoinAIError("No hay proveedor IA configurado para este usuario")
        system = ("Analiza un proyecto cripto usando exclusivamente el JSON entregado. "
                  "No inventes hechos, inversores, propietarios, gobiernos ni URLs. "
                  "No infieras afiliación política. Una relación institucional solo puede "
                  "mencionarse con entity_id y evidence_ids presentes. Devuelve SOLO JSON válido.")
        try:
            response = requests.post(f"{classifier.base_url}/chat/completions", headers={"Authorization": f"Bearer {classifier.api_key}", "Content-Type": "application/json"}, json={"model": classifier.modelo, "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}], "temperature": 0, "response_format": {"type": "json_object"}}, timeout=60)
            response.raise_for_status()
            return json.loads(response.json()["choices"][0]["message"]["content"])
        except Exception as exc:
            raise AltcoinAIError("El proveedor IA no respondió con un análisis válido") from exc


def analyze_project(project, user, analyst=None):
    payload = _payload(project, user)
    input_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
    current = AltcoinAIAnalysis.objects.filter(user=user, project=project, input_hash=input_hash, expires_at__gt=timezone.now()).first()
    if current:
        return current.structured_result
    result = (analyst or DefaultAltcoinAnalyst()).analyze(payload, user)
    allowed_entities = {item["entity_id"] for item in payload["verified_relationships"]}
    result = _validate_result(result, set(payload["allowed_evidence_ids"]), allowed_entities)
    classifier = _clasificador_usuario(user)
    AltcoinAIAnalysis.objects.update_or_create(user=user, project=project, input_hash=input_hash, defaults={"prompt_version": PROMPT_VERSION, "model_name": getattr(classifier, "modelo", "unknown"), "structured_result": result, "expires_at": timezone.now() + timedelta(days=7)})
    return result
