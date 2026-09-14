from django.contrib import admin

from .models import (
    AltcoinAIAnalysis,
    AltcoinCriterionAssessment,
    AltcoinCriterionOverride,
    AltcoinDiscoveryCandidate,
    AltcoinExternalId,
    AltcoinMarketSnapshot,
    AltcoinProject,
    AltcoinProjectTheme,
    AltcoinResearchPacket,
    AltcoinScoreSnapshot,
    AltcoinSyncRun,
    AltcoinTheme,
    AltcoinWatchlist,
    EntityRelationship,
    EvidenceRecord,
    InstitutionalAssessment,
    InstitutionalEntity,
    InstitutionalEntityAlias,
    ProjectEntityRelationship,
)


@admin.register(AltcoinProject)
class AltcoinProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "symbol", "universe_position", "in_universe", "active")
    list_filter = ("in_universe", "active")
    search_fields = ("name", "symbol", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(InstitutionalEntity)
class InstitutionalEntityAdmin(admin.ModelAdmin):
    list_display = ("canonical_name", "entity_type", "country_code", "verified", "active")
    list_filter = ("entity_type", "verified", "active")
    search_fields = ("canonical_name", "normalized_name", "slug")


@admin.register(ProjectEntityRelationship)
class ProjectEntityRelationshipAdmin(admin.ModelAdmin):
    list_display = ("project", "entity", "relationship_type", "status", "confidence", "reviewed_at")
    list_filter = ("relationship_type", "status")
    search_fields = ("project__name", "project__symbol", "entity__canonical_name")
    readonly_fields = ("reviewed_at",)


for model in (
    AltcoinTheme, AltcoinProjectTheme, AltcoinExternalId, AltcoinMarketSnapshot,
    AltcoinCriterionAssessment, AltcoinScoreSnapshot, EvidenceRecord,
    EntityRelationship, InstitutionalAssessment, InstitutionalEntityAlias,
    AltcoinWatchlist, AltcoinCriterionOverride, AltcoinAIAnalysis, AltcoinSyncRun,
    AltcoinDiscoveryCandidate, AltcoinResearchPacket,
):
    admin.site.register(model)
