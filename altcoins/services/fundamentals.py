from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

import requests
from django.utils import timezone

from banca.services.clasificacion_ia import _clasificador_usuario

from altcoins.constants import FUNDAMENTAL_CRITERIA, POLICY
from altcoins.models import AltcoinCriterionAssessment, AltcoinResearchPacket
from altcoins.services.score_snapshots import recalculate_project_score


PROMPT_VERSION = "2026-08-v1"
ALLOWED_STATUSES = {choice[0] for choice in AltcoinCriterionAssessment.STATUS_CHOICES}


class FundamentalEvaluationError(ValueError):
    pass


def _packet_payload(project):
    packet = project.research_packets.filter(status="SUCCESS").prefetch_related("evidence").order_by("-finished_at", "-started_at").first()
    if not packet:
        raise FundamentalEvaluationError("Primero investiga el proyecto para construir su dossier.")
    evidence = [
        {"id": item.id, "title": item.title, "url": item.source_url, "tier": item.reliability_tier, "excerpt": item.excerpt}
        for item in packet.evidence.filter(active=True)
    ]
    payload = {
        "project": {"id": project.id, "name": project.name, "symbol": project.symbol, "website": project.website},
        "dossier": packet.payload,
        "evidence": evidence,
        "allowed_evidence_ids": [item["id"] for item in evidence],
        "policy": {"version": POLICY.version, "criteria": [code for code, _label in FUNDAMENTAL_CRITERIA]},
    }
    return packet, payload


def _validate_result(result, allowed_ids):
    if not isinstance(result, dict):
        raise FundamentalEvaluationError("La IA no devolvió un objeto JSON.")
    criteria = result.get("criteria", {})
    if not isinstance(criteria, dict):
        raise FundamentalEvaluationError("La IA no devolvió el bloque criteria.")
    clean = {}
    for code, _label in FUNDAMENTAL_CRITERIA:
        item = criteria.get(code, {})
        if not isinstance(item, dict):
            item = {}
        status = item.get("status", "UNKNOWN")
        if status not in ALLOWED_STATUSES:
            status = "UNKNOWN"
        evidence_ids = item.get("evidence_ids", [])
        if not isinstance(evidence_ids, list):
            evidence_ids = []
        if any(evidence_id not in allowed_ids for evidence_id in evidence_ids):
            raise FundamentalEvaluationError("La IA citó una evidencia que no pertenece al dossier.")
        try:
            raw_confidence = Decimal(str(item.get("confidence", 0)))
            confidence = min(Decimal("100"), max(Decimal("0"), raw_confidence))
        except (InvalidOperation, TypeError, ValueError):
            confidence = Decimal("0")
        clean[code] = {
            "status": status,
            "confidence": confidence.quantize(Decimal("0.01")),
            "rationale": str(item.get("rationale", ""))[:2000],
            "evidence_ids": evidence_ids[:20],
        }
    return clean


class DefaultFundamentalEvaluator:
    def evaluate(self, payload, user):
        classifier = _clasificador_usuario(user)
        if not classifier.disponible():
            raise FundamentalEvaluationError("No hay proveedor IA configurado para este usuario.")
        system = (
            "Evalúa fundamentos de un proyecto cripto usando exclusivamente el dossier JSON. "
            "Devuelve SOLO JSON válido con {criteria:{CODE:{status,confidence,rationale,evidence_ids}}}. "
            "Usa exactamente los códigos entregados. status debe ser PASS, PARTIAL, FAIL o UNKNOWN. "
            "Si el dossier no demuestra un criterio, usa UNKNOWN. No inventes datos ni instituciones. "
            "Cada evidence_id debe existir en allowed_evidence_ids."
        )
        try:
            response = requests.post(
                f"{classifier.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {classifier.api_key}", "Content-Type": "application/json"},
                json={"model": classifier.modelo, "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}], "temperature": 0, "response_format": {"type": "json_object"}},
                timeout=60,
            )
            response.raise_for_status()
            return json.loads(response.json()["choices"][0]["message"]["content"])
        except Exception as exc:
            raise FundamentalEvaluationError("El proveedor IA no respondió con una evaluación válida.") from exc


def evaluate_fundamentals(project, user, evaluator=None):
    packet, payload = _packet_payload(project)
    input_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
    existing = {item.criterion_code: item for item in project.criterion_assessments.all()}
    result = (evaluator or DefaultFundamentalEvaluator()).evaluate(payload, user)
    clean = _validate_result(result, set(payload["allowed_evidence_ids"]))
    for code, item in clean.items():
        # An UNKNOWN model answer must not erase a deterministic supply/valuation
        # fact already calculated from the market payload.
        if item["status"] == "UNKNOWN" and existing.get(code) and existing[code].source_mode == "AUTOMATIC":
            continue
        AltcoinCriterionAssessment.objects.update_or_create(
            project=project,
            criterion_code=code,
            defaults={
                "status": item["status"],
                "confidence": item["confidence"],
                "source_mode": "AI_ASSISTED",
                "rationale": item["rationale"],
                "input_hash": input_hash,
                "policy_version": POLICY.version,
            },
        )
    recalculate_project_score(project)
    return {"packet_id": packet.id, "input_hash": input_hash, "criteria": clean}
