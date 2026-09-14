import hashlib

from django.db import transaction
from django.utils.text import slugify

from altcoins.models import EvidenceRecord, InstitutionalEntity, ProjectEntityRelationship


def _available_entity_slug(name):
    base = slugify(name)[:220] or "entidad"
    slug = base
    suffix = 2
    while InstitutionalEntity.objects.filter(slug=slug).exists():
        slug = f"{base[:215]}-{suffix}"
        suffix += 1
    return slug


@transaction.atomic
def register_relationship_candidate(*, project, entity_name, entity_type, relationship_type, source_url, title="", excerpt="", reliability_tier="D_UNVERIFIED", created_by=None):
    """Registra evidencia manual; la relación nunca queda verificada automáticamente."""
    canonical_name = " ".join(entity_name.split())
    normalized_name = canonical_name.casefold()
    entity = InstitutionalEntity.objects.filter(normalized_name=normalized_name).first()
    if entity is None:
        entity = InstitutionalEntity.objects.create(
            canonical_name=canonical_name,
            normalized_name=normalized_name,
            slug=_available_entity_slug(canonical_name),
            entity_type=entity_type,
        )
    elif entity.entity_type == "UNKNOWN" and entity_type != "UNKNOWN":
        entity.entity_type = entity_type
        entity.save(update_fields=["entity_type"])

    content_hash = hashlib.sha256(f"{title}\n{excerpt}".encode("utf-8")).hexdigest()
    evidence, _ = EvidenceRecord.objects.get_or_create(
        source_url=source_url,
        content_hash=content_hash,
        defaults={
            "source_type": "MANUAL",
            "title": title,
            "excerpt": excerpt[:2000],
            "reliability_tier": reliability_tier,
        },
    )
    relation, _ = ProjectEntityRelationship.objects.get_or_create(
        project=project,
        entity=entity,
        relationship_type=relationship_type,
        announced_at=None,
        defaults={"status": "CANDIDATE", "confidence": 50, "created_by": created_by},
    )
    relation.evidence.add(evidence)
    return relation
