from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (
    AltcoinCriterionAssessment,
    AltcoinCriterionOverride,
    AltcoinAIAnalysis,
    AltcoinDiscoveryCandidate,
    AltcoinMarketSnapshot,
    AltcoinProject,
    AltcoinSyncRun,
    AltcoinWatchlist,
    InstitutionalEntity,
    ProjectEntityRelationship,
)
from .forms import DiscoverySearchForm, InstitutionalResearchForm
from .services.institutional_scoring import assess_project_institutional_footprint
from .services.metadata import sync_coinmarketcap_metadata
from .services.providers.coinmarketcap import CoinMarketCapProvider
from .services.providers.coingecko import CoinGeckoProvider
from .services.research import register_relationship_candidate
from .services.score_snapshots import recalculate_all_scores
from .services.discovery import DiscoveryError, discover_by_query, discover_robinhood, promote_candidate, research_candidate
from .services.fundamentals import FundamentalEvaluationError, evaluate_fundamentals
from .services.scoring import FUNDAMENTAL_CRITERIA, calculate_fundamental_score, trap_signals
from .services.ai_analysis import AltcoinAIError, analyze_project
from .services.sync import sync_market
from configuracion.models import CredencialIntegracion
from configuracion.services.integrations import get_coinmarketcap_api_key, get_integration_api_key


logger = logging.getLogger(__name__)


def _fundamental_context(project, user):
    assessments = {item.criterion_code: item for item in project.criterion_assessments.all()}
    overrides = {item.criterion_code: item for item in AltcoinCriterionOverride.objects.filter(user=user, project=project)}
    statuses = {code: (overrides[code].status if code in overrides else assessments.get(code).status if code in assessments else "UNKNOWN") for code, _label in FUNDAMENTAL_CRITERIA}
    result = calculate_fundamental_score(statuses)
    return statuses, result, trap_signals(statuses)


@login_required
def scanner(request):
    projects = AltcoinProject.objects.filter(active=True, in_universe=True).prefetch_related("theme_links__theme", "institutional_relationships__entity")
    query = request.GET.get("q", "").strip()
    theme = request.GET.get("theme", "").strip()
    relation_type = request.GET.get("relation", "").strip()
    entity_type = request.GET.get("entity_type", "").strip()
    if query:
        projects = projects.filter(Q(name__icontains=query) | Q(symbol__icontains=query) | Q(institutional_relationships__entity__canonical_name__icontains=query) | Q(institutional_relationships__entity__aliases__alias__icontains=query)).distinct()
    if theme:
        projects = projects.filter(theme_links__theme__slug=theme)
    if relation_type:
        projects = projects.filter(institutional_relationships__relationship_type=relation_type, institutional_relationships__status="VERIFIED")
    if entity_type:
        projects = projects.filter(institutional_relationships__entity__entity_type=entity_type, institutional_relationships__status="VERIFIED")
    rows = []
    for project in projects:
        statuses, fundamental, traps = _fundamental_context(project, request.user)
        institutional = assess_project_institutional_footprint(project)
        latest_market = project.market_snapshots.order_by("-observed_at").first()
        verified_relationships = project.institutional_relationships.filter(status="VERIFIED").select_related("entity")[:3]
        rows.append({"project": project, "fundamental": fundamental, "institutional": institutional, "traps": traps, "market": latest_market, "relationships": verified_relationships, "watched": AltcoinWatchlist.objects.filter(user=request.user, project=project).exists()})
    project_count = AltcoinProject.objects.filter(active=True, in_universe=True).count()
    candidates = AltcoinDiscoveryCandidate.objects.exclude(status="REJECTED").select_related("promoted_project").order_by("status", "-last_seen_at")[:50]
    operations = None
    if request.user.is_staff:
        operations = {
            "project_count": project_count,
            "snapshot_count": AltcoinMarketSnapshot.objects.count(),
            "last_market_at": AltcoinMarketSnapshot.objects.order_by("-observed_at").values_list("observed_at", flat=True).first(),
            "last_sync": AltcoinSyncRun.objects.order_by("-started_at").first(),
            "pending_relations": ProjectEntityRelationship.objects.filter(status="CANDIDATE").count(),
            "cmc_configured": bool(get_coinmarketcap_api_key()),
            "discovery_count": AltcoinDiscoveryCandidate.objects.exclude(status="REJECTED").count(),
            "researched_count": AltcoinDiscoveryCandidate.objects.filter(status="RESEARCHED").count(),
            "brave_configured": bool(get_integration_api_key(CredencialIntegracion.PROVIDER_BRAVE_SEARCH)),
        }
    return render(request, "altcoins/scanner.html", {
        "rows": rows,
        "query": query,
        "themes": project_themes(),
        "relation_choices": ProjectEntityRelationship.RELATION_CHOICES,
        "entity_choices": InstitutionalEntity.ENTITY_CHOICES,
        "project_count": project_count,
        "operations": operations,
        "research_form": InstitutionalResearchForm() if request.user.is_staff else None,
        "discovery_form": DiscoverySearchForm() if request.user.is_staff else None,
        "discovery_candidates": candidates if request.user.is_staff else [],
    })


def project_themes():
    from .models import AltcoinTheme
    return AltcoinTheme.objects.filter(active=True).order_by("name")


@user_passes_test(lambda user: user.is_staff)
@require_POST
def run_operation(request, action):
    if action not in {"bootstrap", "market", "metadata", "scores"}:
        messages.error(request, "Operación no reconocida.")
        return redirect("altcoins:scanner")
    try:
        if action == "bootstrap":
            run = sync_market(provider=CoinGeckoProvider(), bootstrap=True, limit=45)
            if run.status == "SUCCESS":
                total = AltcoinProject.objects.filter(active=True, in_universe=True).count()
                messages.success(request, f"Scanner inicializado: {total} proyectos y {run.records_updated} precios actualizados.")
            else:
                messages.warning(request, run.error_message or "La inicialización terminó parcialmente.")
        elif action == "market":
            run = sync_market(provider=CoinGeckoProvider())
            if run.status == "SUCCESS":
                messages.success(request, f"Mercado actualizado para {run.records_updated} proyectos.")
            else:
                messages.warning(request, run.error_message or "La actualización terminó parcialmente.")
        elif action == "metadata":
            api_key = get_coinmarketcap_api_key()
            if not api_key:
                messages.warning(request, "CoinMarketCap no está configurado. Agrega su API key en Configuración → Integraciones de mercado.")
            else:
                result = sync_coinmarketcap_metadata(CoinMarketCapProvider(api_key=api_key))
                messages.success(request, f"Metadata validada: {result['matched']} coincidencias y {result['rejected']} casos pendientes.")
        else:
            total = recalculate_all_scores()
            if total:
                messages.success(request, f"Scores recalculados para {total} proyectos.")
            else:
                messages.warning(request, "Primero inicializa el universo de proyectos.")
    except Exception as exc:
        logger.exception("Falló la operación %s del Altcoin Scanner", action)
        messages.error(request, f"No se pudo completar la operación: {str(exc)[:240]}")
    return redirect("altcoins:scanner")


@user_passes_test(lambda user: user.is_staff)
@require_POST
def discover(request):
    form = DiscoverySearchForm(request.POST)
    if not form.is_valid():
        detail = next(iter(form.errors.values()))[0] if form.errors else "Revisa la búsqueda."
        messages.error(request, f"No se pudo iniciar el descubrimiento: {detail}")
        return redirect("altcoins:scanner")
    try:
        source = form.cleaned_data["source"]
        if source == "ROBINHOOD_PUBLIC":
            rows = discover_robinhood()
            label = "Robinhood"
        else:
            rows = discover_by_query(form.cleaned_data["query"], source=source, limit=20)
            label = "CoinGecko" if source == "COINGECKO_SEARCH" else "la web"
        messages.success(request, f"Descubrimiento completado desde {label}: {len(rows)} candidatos nuevos o actualizados.")
    except DiscoveryError as exc:
        messages.error(request, str(exc))
    except Exception as exc:
        logger.exception("Falló el descubrimiento de altcoins")
        messages.error(request, f"No se pudo consultar la fuente: {str(exc)[:240]}")
    return redirect("altcoins:scanner")


@user_passes_test(lambda user: user.is_staff)
@require_POST
def research_discovery_candidate(request, pk):
    candidate = get_object_or_404(AltcoinDiscoveryCandidate, pk=pk)
    try:
        packet = research_candidate(candidate)
        messages.success(request, f"Dossier listo para {candidate.name}: {packet.evidence_count} evidencias y {packet.source_count} fuentes procesadas.")
    except DiscoveryError as exc:
        messages.error(request, str(exc))
    except Exception as exc:
        logger.exception("Falló la investigación del candidato %s", candidate.pk)
        messages.error(request, f"No se pudo investigar el candidato: {str(exc)[:240]}")
    return redirect("altcoins:scanner")


@user_passes_test(lambda user: user.is_staff)
@require_POST
def promote_discovery_candidate(request, pk):
    candidate = get_object_or_404(AltcoinDiscoveryCandidate, pk=pk)
    try:
        project = promote_candidate(candidate)
        messages.success(request, f"{project.name} fue promovido al scanner como proyecto #{project.universe_position}.")
    except Exception as exc:
        logger.exception("Falló la promoción del candidato %s", candidate.pk)
        messages.error(request, f"No se pudo promover el candidato: {str(exc)[:240]}")
    return redirect("altcoins:scanner")


@user_passes_test(lambda user: user.is_staff)
@require_POST
def register_institutional_evidence(request):
    form = InstitutionalResearchForm(request.POST)
    if not form.is_valid():
        detail = next(iter(form.errors.values()))[0] if form.errors else "Revisa los datos ingresados."
        messages.error(request, f"No se pudo registrar la evidencia: {detail}")
        return redirect("altcoins:scanner")
    relation = register_relationship_candidate(
        **form.cleaned_data,
        created_by=request.user,
    )
    messages.success(request, f"Evidencia guardada. La relación #{relation.pk} quedó pendiente de revisión humana.")
    return redirect("altcoins:scanner")


@login_required
def project_detail(request, slug):
    project = get_object_or_404(AltcoinProject.objects.prefetch_related("theme_links__theme", "criterion_assessments", "institutional_relationships__entity"), slug=slug, active=True)
    statuses, fundamental, traps = _fundamental_context(project, request.user)
    institutional = assess_project_institutional_footprint(project)
    return render(request, "altcoins/project_detail.html", {"project": project, "instrument": project.instrument, "statuses": statuses, "criteria": FUNDAMENTAL_CRITERIA, "fundamental": fundamental, "institutional": institutional, "traps": traps, "relationships": project.institutional_relationships.filter(status="VERIFIED").select_related("entity").prefetch_related("evidence"), "market": project.market_snapshots.order_by("-observed_at").first(), "watched": AltcoinWatchlist.objects.filter(user=request.user, project=project).exists(), "ai_analysis": AltcoinAIAnalysis.objects.filter(user=request.user, project=project).order_by("-generated_at").first(), "research_packet": project.research_packets.filter(status__in=["SUCCESS", "PARTIAL"]).prefetch_related("evidence").order_by("-finished_at").first()})


@login_required
def institutions(request):
    query = request.GET.get("q", "").strip()
    entities = InstitutionalEntity.objects.filter(active=True, project_relationships__status="VERIFIED").distinct()
    if query:
        entities = entities.filter(Q(canonical_name__icontains=query) | Q(aliases__alias__icontains=query))
    return render(request, "altcoins/institutions.html", {"entities": entities.order_by("canonical_name"), "query": query})


@login_required
def institution_detail(request, slug):
    entity = get_object_or_404(InstitutionalEntity, slug=slug, active=True)
    relationships = entity.project_relationships.filter(status="VERIFIED").select_related("project").prefetch_related("evidence")
    return render(request, "altcoins/institution_detail.html", {"entity": entity, "relationships": relationships, "entity_relationships": entity.outgoing_entity_relationships.filter(status="VERIFIED").select_related("object_entity")})


@login_required
@require_POST
def toggle_watchlist(request, slug):
    project = get_object_or_404(AltcoinProject, slug=slug, active=True)
    item, created = AltcoinWatchlist.objects.get_or_create(user=request.user, project=project)
    if not created:
        item.delete()
        messages.info(request, "Proyecto eliminado de tu watchlist.")
    else:
        messages.success(request, "Proyecto agregado a tu watchlist.")
    return redirect(request.POST.get("next") or reverse("altcoins:project_detail", args=[project.slug]))


@login_required
@require_POST
def set_criterion_override(request, slug):
    project = get_object_or_404(AltcoinProject, slug=slug, active=True)
    criterion = request.POST.get("criterion_code", "")
    status = request.POST.get("status", "UNKNOWN")
    if criterion not in {code for code, _label in FUNDAMENTAL_CRITERIA} or status not in {choice[0] for choice in AltcoinCriterionAssessment.STATUS_CHOICES}:
        messages.error(request, "Criterio o estado inválido.")
        return redirect("altcoins:project_detail", slug=project.slug)
    AltcoinCriterionOverride.objects.update_or_create(user=request.user, project=project, criterion_code=criterion, defaults={"status": status, "rationale": request.POST.get("rationale", "")[:2000]})
    messages.success(request, "Override personal guardado.")
    return redirect("altcoins:project_detail", slug=project.slug)


@login_required
@require_POST
def generate_ai_analysis(request, slug):
    project = get_object_or_404(AltcoinProject, slug=slug, active=True)
    try:
        analyze_project(project, request.user)
        messages.success(request, "Análisis IA generado con evidencias verificadas.")
    except AltcoinAIError as exc:
        messages.error(request, str(exc))
    return redirect("altcoins:project_detail", slug=project.slug)


@user_passes_test(lambda user: user.is_staff)
@require_POST
def evaluate_project_fundamentals(request, slug):
    project = get_object_or_404(AltcoinProject, slug=slug, active=True)
    try:
        result = evaluate_fundamentals(project, request.user)
        known = sum(1 for item in result["criteria"].values() if item["status"] != "UNKNOWN")
        messages.success(request, f"Evaluación guardada: {known} de {len(result['criteria'])} criterios con evidencia o datos suficientes.")
    except FundamentalEvaluationError as exc:
        messages.error(request, str(exc))
    except Exception as exc:
        logger.exception("Falló la evaluación fundamental de %s", project.slug)
        messages.error(request, f"No se pudo evaluar el proyecto: {str(exc)[:240]}")
    return redirect("altcoins:project_detail", slug=project.slug)


@user_passes_test(lambda user: user.is_staff)
def review_queue(request):
    relationships = ProjectEntityRelationship.objects.filter(status="CANDIDATE").select_related("project", "entity").prefetch_related("evidence").order_by("-confidence")
    return render(request, "altcoins/review_queue.html", {"relationships": relationships})


@user_passes_test(lambda user: user.is_staff)
@require_POST
def review_relationship(request, pk):
    relationship = get_object_or_404(ProjectEntityRelationship, pk=pk)
    action = request.POST.get("action")
    if action not in {"VERIFIED", "REJECTED", "EXPIRED"}:
        messages.error(request, "Acción inválida.")
    else:
        relationship.status = action
        relationship.reviewed_by = request.user
        relationship.reviewed_at = timezone.now()
        relationship.save(update_fields=["status", "reviewed_by", "reviewed_at"])
        messages.success(request, "Relación revisada.")
    return redirect("altcoins:review_queue")
