from __future__ import annotations

from django.conf import settings
from django.db import models


class AltcoinProject(models.Model):
    instrument = models.OneToOneField("trading.Instrument", on_delete=models.SET_NULL, null=True, blank=True, related_name="altcoin_project")
    name = models.CharField(max_length=160)
    symbol = models.CharField(max_length=32)
    slug = models.SlugField(max_length=180, unique=True)
    website = models.URLField(blank=True)
    official_domain = models.CharField(max_length=180, blank=True)
    coingecko_id = models.CharField(max_length=160, blank=True, null=True, unique=True)
    coinmarketcap_id = models.PositiveBigIntegerField(blank=True, null=True, unique=True)
    contract_address = models.CharField(max_length=180, blank=True)
    chain = models.CharField(max_length=80, blank=True)
    launch_date = models.DateField(blank=True, null=True)
    active = models.BooleanField(default=True)
    in_universe = models.BooleanField(default=False)
    universe_position = models.PositiveIntegerField(blank=True, null=True)
    universe_added_at = models.DateTimeField(blank=True, null=True)
    metadata_updated_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("universe_position", "name")

    def __str__(self):
        return f"{self.name} ({self.symbol.upper()})"


class AltcoinDiscoveryCandidate(models.Model):
    """Candidate discovered outside the legacy market-cap universe.

    Candidates stay in a review queue until their sources have been collected and
    a staff member promotes them to the scanner. This keeps discovery separate
    from the historical top-45 bootstrap.
    """

    SOURCE_CHOICES = (
        ("COINGECKO_SEARCH", "Búsqueda CoinGecko"),
        ("ROBINHOOD_PUBLIC", "Catálogo público Robinhood"),
        ("WEB_SEARCH", "Búsqueda web"),
    )
    STATUS_CHOICES = (
        ("NEW", "Nuevo"),
        ("RESEARCHED", "Investigado"),
        ("PROMOTED", "Promovido al scanner"),
        ("REJECTED", "Descartado"),
        ("ERROR", "Error de investigación"),
    )
    source = models.CharField(max_length=24, choices=SOURCE_CHOICES)
    external_id = models.CharField(max_length=180)
    name = models.CharField(max_length=160)
    symbol = models.CharField(max_length=32)
    coingecko_id = models.CharField(max_length=160, blank=True)
    website = models.URLField(blank=True)
    source_url = models.URLField(max_length=1000, blank=True)
    search_query = models.CharField(max_length=220, blank=True)
    market_cap_rank = models.PositiveIntegerField(blank=True, null=True)
    market_cap_usd = models.DecimalField(max_digits=30, decimal_places=2, blank=True, null=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="NEW")
    promoted_project = models.ForeignKey(
        "AltcoinProject", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="discovery_candidates",
    )
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ("-last_seen_at", "name")
        constraints = [
            models.UniqueConstraint(
                fields=("source", "external_id"),
                name="unique_altcoin_discovery_source_id",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.symbol.upper()}) · {self.get_source_display()}"


class AltcoinResearchPacket(models.Model):
    """Versioned, normalized dossier used as the first input for evaluation."""

    STATUS_CHOICES = (
        ("RUNNING", "Investigando"),
        ("SUCCESS", "Completo"),
        ("PARTIAL", "Parcial"),
        ("FAILED", "Fallido"),
    )
    candidate = models.ForeignKey(AltcoinDiscoveryCandidate, on_delete=models.CASCADE, related_name="research_packets")
    project = models.ForeignKey(AltcoinProject, on_delete=models.SET_NULL, null=True, blank=True, related_name="research_packets")
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="RUNNING")
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    input_hash = models.CharField(max_length=64, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    source_count = models.PositiveIntegerField(default=0)
    evidence_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
    evidence = models.ManyToManyField("EvidenceRecord", blank=True, related_name="research_packets")

    class Meta:
        ordering = ("-started_at",)


class AltcoinTheme(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=110, unique=True)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class AltcoinProjectTheme(models.Model):
    project = models.ForeignKey(AltcoinProject, on_delete=models.CASCADE, related_name="theme_links")
    theme = models.ForeignKey(AltcoinTheme, on_delete=models.CASCADE, related_name="project_links")
    is_primary = models.BooleanField(default=False)
    confidence = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    source = models.CharField(max_length=32, default="MANUAL")

    class Meta:
        constraints = [models.UniqueConstraint(fields=("project", "theme"), name="unique_altcoin_project_theme")]


class AltcoinExternalId(models.Model):
    PROVIDER_CHOICES = (("COINGECKO", "CoinGecko"), ("CMC", "CoinMarketCap"))
    project = models.ForeignKey(AltcoinProject, on_delete=models.CASCADE, related_name="external_ids")
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES)
    external_id = models.CharField(max_length=180)
    contract_address = models.CharField(max_length=180, blank=True)
    chain = models.CharField(max_length=80, blank=True)
    verified = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("provider", "external_id"), name="unique_altcoin_external_id")]


class AltcoinMarketSnapshot(models.Model):
    project = models.ForeignKey(AltcoinProject, on_delete=models.CASCADE, related_name="market_snapshots")
    observed_at = models.DateTimeField()
    price_usd = models.DecimalField(max_digits=30, decimal_places=12, blank=True, null=True)
    market_cap_usd = models.DecimalField(max_digits=30, decimal_places=2, blank=True, null=True)
    fdv_usd = models.DecimalField(max_digits=30, decimal_places=2, blank=True, null=True)
    volume_24h_usd = models.DecimalField(max_digits=30, decimal_places=2, blank=True, null=True)
    circulating_supply = models.DecimalField(max_digits=40, decimal_places=12, blank=True, null=True)
    total_supply = models.DecimalField(max_digits=40, decimal_places=12, blank=True, null=True)
    max_supply = models.DecimalField(max_digits=40, decimal_places=12, blank=True, null=True)
    ath_usd = models.DecimalField(max_digits=30, decimal_places=12, blank=True, null=True)
    ath_date = models.DateTimeField(blank=True, null=True)
    ath_drawdown_pct = models.DecimalField(max_digits=8, decimal_places=4, blank=True, null=True)
    market_cap_rank = models.PositiveIntegerField(blank=True, null=True)
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("-observed_at",)
        constraints = [models.UniqueConstraint(fields=("project", "observed_at"), name="unique_altcoin_market_snapshot")]


class AltcoinCriterionAssessment(models.Model):
    STATUS_CHOICES = (("PASS", "Pass"), ("PARTIAL", "Parcial"), ("FAIL", "Fail"), ("UNKNOWN", "Sin datos"))
    SOURCE_CHOICES = (("AUTOMATIC", "Automático"), ("AI_ASSISTED", "IA asistida"), ("MANUAL", "Manual"), ("USER_OVERRIDE", "Override"))
    project = models.ForeignKey(AltcoinProject, on_delete=models.CASCADE, related_name="criterion_assessments")
    criterion_code = models.CharField(max_length=32)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="UNKNOWN")
    confidence = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    source_mode = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="AUTOMATIC")
    rationale = models.TextField(blank=True)
    calculated_at = models.DateTimeField(auto_now=True)
    input_hash = models.CharField(max_length=64, blank=True)
    policy_version = models.CharField(max_length=40, default="2026-08-v1")

    class Meta:
        constraints = [models.UniqueConstraint(fields=("project", "criterion_code"), name="unique_altcoin_criterion")]


class AltcoinScoreSnapshot(models.Model):
    project = models.ForeignKey(AltcoinProject, on_delete=models.CASCADE, related_name="score_snapshots")
    calculated_at = models.DateTimeField(auto_now_add=True)
    fundamental_score = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    coverage_pct = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    classification = models.CharField(max_length=20, default="SIN_DATOS")
    criteria_snapshot = models.JSONField(default=dict, blank=True)
    policy_version = models.CharField(max_length=40, default="2026-08-v1")


class InstitutionalEntity(models.Model):
    ENTITY_CHOICES = (
        ("PERSON", "Persona"), ("PUBLIC_COMPANY", "Empresa pública"),
        ("PRIVATE_COMPANY", "Empresa privada"), ("VC_FUND", "Fondo VC"),
        ("FOUNDATION", "Fundación"), ("DAO", "DAO"), ("BANK", "Banco"),
        ("UNIVERSITY", "Universidad"), ("GOVERNMENT_AGENCY", "Organismo público"),
        ("STATE_OWNED_COMPANY", "Empresa estatal"), ("MULTILATERAL", "Multilateral"),
        ("NONPROFIT", "ONG"), ("UNKNOWN", "Desconocida"),
    )
    canonical_name = models.CharField(max_length=220)
    normalized_name = models.CharField(max_length=220, unique=True)
    slug = models.SlugField(max_length=240, unique=True)
    entity_type = models.CharField(max_length=32, choices=ENTITY_CHOICES, default="UNKNOWN")
    country_code = models.CharField(max_length=2, blank=True)
    website = models.URLField(blank=True)
    official_domain = models.CharField(max_length=180, blank=True)
    lei = models.CharField(max_length=32, blank=True)
    sec_cik = models.CharField(max_length=20, blank=True)
    usaspending_uei = models.CharField(max_length=32, blank=True)
    verified = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.canonical_name


class InstitutionalEntityAlias(models.Model):
    entity = models.ForeignKey(InstitutionalEntity, on_delete=models.CASCADE, related_name="aliases")
    alias = models.CharField(max_length=220)
    normalized_alias = models.CharField(max_length=220)
    source = models.CharField(max_length=100, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("entity", "normalized_alias"), name="unique_entity_alias")]


class EvidenceRecord(models.Model):
    SOURCE_CHOICES = (
        ("COINGECKO", "CoinGecko"), ("CMC", "CoinMarketCap"),
        ("ROBINHOOD_PUBLIC", "Robinhood público"), ("WEB_SEARCH", "Búsqueda web"),
        ("SOCIAL", "Red social"),
        ("PROJECT_OFFICIAL", "Proyecto oficial"), ("INVESTOR_OFFICIAL", "Inversor oficial"),
        ("SEC_EDGAR", "SEC EDGAR"), ("USASPENDING", "USAspending"),
        ("DEFILLAMA", "DefiLlama"), ("TOKENOMIST", "Tokenomist"), ("MANUAL", "Manual"),
    )
    RELIABILITY_CHOICES = (("A_PRIMARY", "Primaria"), ("B_CORROBORATED", "Corroborada"), ("C_SECONDARY", "Secundaria"), ("D_UNVERIFIED", "No verificada"))
    source_type = models.CharField(max_length=32, choices=SOURCE_CHOICES)
    source_url = models.URLField(max_length=1000)
    title = models.CharField(max_length=300, blank=True)
    publisher = models.CharField(max_length=180, blank=True)
    published_at = models.DateTimeField(blank=True, null=True)
    retrieved_at = models.DateTimeField(auto_now=True)
    excerpt = models.TextField(max_length=2000, blank=True)
    content_hash = models.CharField(max_length=64)
    reliability_tier = models.CharField(max_length=20, choices=RELIABILITY_CHOICES, default="D_UNVERIFIED")
    raw_payload = models.JSONField(default=dict, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("source_url", "content_hash"), name="unique_evidence_version")]


class ProjectEntityRelationship(models.Model):
    RELATION_CHOICES = (
        ("FOUNDER", "Fundador"), ("CORE_TEAM", "Equipo principal"), ("LEGAL_OPERATOR", "Operador legal"),
        ("FOUNDATION", "Fundación"), ("PARENT_COMPANY", "Empresa matriz"),
        ("EQUITY_INVESTOR", "Inversor de equity"), ("TOKEN_INVESTOR", "Inversor de tokens"),
        ("LEAD_INVESTOR", "Inversor principal"), ("STRATEGIC_BACKER", "Respaldo estratégico"),
        ("GRANT_PROVIDER", "Proveedor de grant"), ("GOVERNMENT_GRANT", "Grant público"),
        ("GOVERNMENT_CONTRACT", "Contrato público"), ("PUBLIC_SECTOR_PILOT", "Piloto público"),
        ("COMMERCIAL_PARTNER", "Partner comercial"), ("ADVISOR", "Advisor"),
        ("CUSTODIAN", "Custodio"), ("MARKET_MAKER", "Market maker"),
        ("EXCHANGE_LISTING", "Listado en exchange"), ("GOVERNANCE_CONTROLLER", "Control de gobernanza"),
    )
    STATUS_CHOICES = (("CANDIDATE", "Candidato"), ("VERIFIED", "Verificado"), ("REJECTED", "Rechazado"), ("EXPIRED", "Expirado"))
    project = models.ForeignKey(AltcoinProject, on_delete=models.CASCADE, related_name="institutional_relationships")
    entity = models.ForeignKey(InstitutionalEntity, on_delete=models.CASCADE, related_name="project_relationships")
    relationship_type = models.CharField(max_length=32, choices=RELATION_CHOICES)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="CANDIDATE")
    announced_at = models.DateTimeField(blank=True, null=True)
    valid_from = models.DateField(blank=True, null=True)
    valid_to = models.DateField(blank=True, null=True)
    amount_usd = models.DecimalField(max_digits=30, decimal_places=2, blank=True, null=True)
    round_name = models.CharField(max_length=120, blank=True)
    is_lead = models.BooleanField(default=False)
    ownership_pct = models.DecimalField(max_digits=7, decimal_places=4, blank=True, null=True)
    token_allocation_pct = models.DecimalField(max_digits=7, decimal_places=4, blank=True, null=True)
    voting_power_pct = models.DecimalField(max_digits=7, decimal_places=4, blank=True, null=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="altcoin_relationships_created")
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="altcoin_relationships_reviewed")
    reviewed_at = models.DateTimeField(blank=True, null=True)
    evidence = models.ManyToManyField(EvidenceRecord, blank=True, related_name="project_relationships")

    class Meta:
        constraints = [models.UniqueConstraint(fields=("project", "entity", "relationship_type", "announced_at"), name="unique_project_entity_relationship")]


class EntityRelationship(models.Model):
    RELATION_CHOICES = (
        ("PARENT_OF", "Matriz de"), ("SUBSIDIARY_OF", "Subsidiaria de"),
        ("STATE_OWNED_BY", "Propiedad estatal de"), ("MANAGED_BY", "Gestionada por"),
        ("FOUNDED_BY", "Fundada por"), ("CURRENT_PUBLIC_ROLE_AT", "Cargo público actual en"),
        ("FORMER_PUBLIC_ROLE_AT", "Cargo público anterior en"), ("AFFILIATED_WITH", "Afiliada a"),
    )
    subject_entity = models.ForeignKey(InstitutionalEntity, on_delete=models.CASCADE, related_name="outgoing_entity_relationships")
    relationship_type = models.CharField(max_length=32, choices=RELATION_CHOICES)
    object_entity = models.ForeignKey(InstitutionalEntity, on_delete=models.CASCADE, related_name="incoming_entity_relationships")
    status = models.CharField(max_length=12, choices=ProjectEntityRelationship.STATUS_CHOICES, default="CANDIDATE")
    valid_from = models.DateField(blank=True, null=True)
    valid_to = models.DateField(blank=True, null=True)
    ownership_pct = models.DecimalField(max_digits=7, decimal_places=4, blank=True, null=True)
    confidence = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    evidence = models.ManyToManyField(EvidenceRecord, blank=True, related_name="entity_relationships")


class InstitutionalAssessment(models.Model):
    project = models.ForeignKey(AltcoinProject, on_delete=models.CASCADE, related_name="institutional_assessments")
    calculated_at = models.DateTimeField(auto_now_add=True)
    institutional_score = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    coverage_pct = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    breakdown = models.JSONField(default=dict, blank=True)
    input_hash = models.CharField(max_length=64, blank=True)
    policy_version = models.CharField(max_length=40, default="2026-08-v1")


class AltcoinWatchlist(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="altcoin_watchlist")
    project = models.ForeignKey(AltcoinProject, on_delete=models.CASCADE, related_name="watchers")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("user", "project"), name="unique_user_altcoin_watchlist")]


class AltcoinCriterionOverride(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="altcoin_overrides")
    project = models.ForeignKey(AltcoinProject, on_delete=models.CASCADE, related_name="user_overrides")
    criterion_code = models.CharField(max_length=32)
    status = models.CharField(max_length=12, choices=AltcoinCriterionAssessment.STATUS_CHOICES)
    rationale = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("user", "project", "criterion_code"), name="unique_user_altcoin_override")]


class AltcoinAIAnalysis(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="altcoin_ai_analyses")
    project = models.ForeignKey(AltcoinProject, on_delete=models.CASCADE, related_name="ai_analyses")
    input_hash = models.CharField(max_length=64)
    prompt_version = models.CharField(max_length=40)
    model_name = models.CharField(max_length=120)
    structured_result = models.JSONField(default=dict)
    generated_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=("user", "project", "input_hash"), name="unique_user_altcoin_ai_input")]


class AltcoinSyncRun(models.Model):
    STATUS_CHOICES = (("RUNNING", "Ejecutando"), ("SUCCESS", "Correcto"), ("PARTIAL", "Parcial"), ("FAILED", "Fallido"))
    provider = models.CharField(max_length=40)
    operation = models.CharField(max_length=80)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="RUNNING")
    records_received = models.PositiveIntegerField(default=0)
    records_created = models.PositiveIntegerField(default=0)
    records_updated = models.PositiveIntegerField(default=0)
    records_rejected = models.PositiveIntegerField(default=0)
    request_credits = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.TextField(blank=True)
    input_hash = models.CharField(max_length=64, blank=True)
