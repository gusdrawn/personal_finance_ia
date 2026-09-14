from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from io import StringIO

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.management import call_command
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .services.ai import run_copilot
from .services.charts import chart_payload
from .indicators import INDICATORS
from .models import DailyBar, Instrument, InstrumentMetricSnapshot, InstrumentUserState, MarketEvent, Strategy, Theme, TradingSyncRun
from .services.scoring import fair_value_upside
from .services.reports import build_asset_report
from .services.providers.twelve_data import ProviderError, TwelveDataProvider
from .services.providers.yahoo import YahooFinanceProvider
from .services.sec import sync_sec_instrument
from .services.sync import sync_symbol


logger = logging.getLogger(__name__)


def _latest_metric(instrument):
    return instrument.metric_snapshots.exclude(source="DEMO").order_by("-observed_at").first()


def _latest_bar(instrument):
    return instrument.daily_bars.exclude(source="DEMO").order_by("-session_date").first()


def _instrument_rows(user, queryset):
    states = {item.instrument_id: item for item in InstrumentUserState.objects.filter(user=user, instrument__in=queryset)}
    rows = []
    for instrument in queryset:
        metric = _latest_metric(instrument)
        bar = _latest_bar(instrument)
        state = states.get(instrument.id)
        price = float(bar.close) if bar else None
        bar_history = instrument.daily_bars.exclude(source="DEMO").order_by("-session_date")
        if bar:
            bar_history = bar_history.filter(source=bar.source)
        previous = bar_history[1:2]
        change = ((price / float(previous[0].close)) - 1) * 100 if price and previous else None
        valuation = (metric.fair_value or metric.target_mean) if metric else None
        upside = fair_value_upside(price, valuation)
        rows.append({"instrument": instrument, "metric": metric, "bar": bar, "price": price, "change": change, "upside": upside, "valuation": valuation, "valuation_label": "Valor justo" if metric and metric.fair_value is not None else "Objetivo analistas", "state": state, "themes": [link.theme for link in instrument.theme_links.all()]})
    return rows


@login_required
def dashboard(request):
    queryset = Instrument.objects.filter(active=True).prefetch_related("theme_links__theme")
    rows = [row for row in _instrument_rows(request.user, queryset) if row["bar"] or row["metric"]]
    rows.sort(key=lambda row: float(row["metric"].quant_score or 0) if row["metric"] else 0, reverse=True)
    indices = [row for row in rows if row["instrument"].symbol in {"SPY", "QQQ", "BTCUSD", "GLD", "USO"}]
    events = MarketEvent.objects.filter(starts_at__gte=timezone.now()).exclude(notes__icontains="demo").order_by("starts_at")[:6]
    operations = None
    if request.user.is_staff:
        operations = {
            "instrument_count": Instrument.objects.filter(active=True, daily_bars__source__in=["YAHOO_FINANCE", "TWELVE_DATA"]).distinct().count(),
            "bar_count": DailyBar.objects.exclude(source="DEMO").count(),
            "last_sync": TradingSyncRun.objects.order_by("-started_at").first(),
            "market_provider": "Twelve Data" if settings.TRADING_TWELVE_DATA_API_KEY else "Yahoo Finance",
            "twelve_data_configured": bool(settings.TRADING_TWELVE_DATA_API_KEY),
            "fmp_configured": bool(settings.TRADING_FMP_API_KEY),
            "fred_configured": bool(settings.TRADING_FRED_API_KEY),
            "fundamental_provider": "SEC EDGAR + FMP" if settings.TRADING_FMP_API_KEY else "SEC EDGAR",
        }
    return render(request, "trading/dashboard.html", {"rows": rows[:10], "indices": indices, "events": events, "indicators": INDICATORS, "operations": operations})


@user_passes_test(lambda user: user.is_staff)
@require_POST
def run_operation(request, action):
    if action not in {"sync_daily", "link_altcoins"}:
        messages.error(request, "Operación de investigación no reconocida.")
        return redirect("trading:dashboard")
    try:
        output = StringIO()
        if action == "sync_daily":
            call_command("sync_trading_data", mode="full", stdout=output, stderr=output)
            run = TradingSyncRun.objects.order_by("-started_at").first()
            if run and run.status == "FAILED":
                messages.error(request, run.error_message or "No se sincronizaron datos reales. Revisa los proveedores configurados.")
            elif run and run.status == "PARTIAL":
                messages.warning(request, f"Actualización parcial: {run.records_received} activos procesados; revisa el registro de sincronización.")
            else:
                total = run.records_received if run else 0
                messages.success(request, f"Mercado actualizado: {total} activos procesados.")
        else:
            call_command("link_altcoins_to_trading", stdout=output)
            messages.success(request, "Altcoins vinculadas con sus fichas del Research Center.")
    except Exception as exc:
        logger.exception("Falló la operación web de Trading %s", action)
        messages.error(request, f"No se pudo completar la operación: {str(exc)[:240]}")
    return redirect("trading:dashboard")


@login_required
def research(request):
    queryset = Instrument.objects.filter(active=True).prefetch_related("theme_links__theme")
    query = request.GET.get("q", "").strip()
    asset_type = request.GET.get("asset_type", "")
    sector = request.GET.get("sector", "")
    theme = request.GET.get("theme", "")
    broker = request.GET.get("broker", "")
    sort = request.GET.get("sort", "quant")
    if query:
        queryset = queryset.filter(Q(symbol__icontains=query) | Q(name__icontains=query) | Q(sector__icontains=query) | Q(industry__icontains=query))
    if asset_type:
        queryset = queryset.filter(asset_type=asset_type)
    if sector:
        queryset = queryset.filter(sector__iexact=sector)
    if theme:
        queryset = queryset.filter(theme_links__theme__slug=theme)
    if broker == "AVAILABLE":
        queryset = queryset.filter(user_states__user=request.user, user_states__broker_status="AVAILABLE")
    elif broker == "UNAVAILABLE":
        queryset = queryset.filter(user_states__user=request.user, user_states__broker_status="UNAVAILABLE")
    rows = _instrument_rows(request.user, queryset.distinct())
    if sort == "upside":
        rows.sort(key=lambda row: row["upside"] if row["upside"] is not None else -999, reverse=True)
    elif sort == "price":
        rows.sort(key=lambda row: row["price"] or 0)
    else:
        rows.sort(key=lambda row: float(row["metric"].quant_score or 0) if row["metric"] else -1, reverse=True)
    sectors = Instrument.objects.filter(active=True).exclude(sector="").values_list("sector", flat=True).distinct().order_by("sector")
    live_enabled = True
    import_symbol = query.upper() if re.fullmatch(r"[A-Za-z0-9.:-]{1,20}", query) else ""
    return render(
        request,
        "trading/research.html",
        {
            "rows": rows,
            "themes": Theme.objects.filter(active=True).order_by("name"),
            "sectors": sectors,
            "asset_types": Instrument.ASSET_TYPES,
            "filters": {"q": query, "asset_type": asset_type, "sector": sector, "theme": theme, "broker": broker, "sort": sort},
            "indicators": INDICATORS,
            "live_enabled": live_enabled,
            "import_symbol": import_symbol,
            "data_mode": "CURRENT",
        },
    )


@login_required
def strategies(request):
    strategy_rows = []
    for strategy in Strategy.objects.filter(active=True):
        strategy_rows.append({"strategy": strategy, "latest": None, "since_activation": None, "constituents": [], "data_available": False})
    return render(request, "trading/strategies.html", {"strategy_rows": strategy_rows})


@login_required
def calendar(request):
    start = date.today()
    events = MarketEvent.objects.filter(starts_at__date__gte=start, starts_at__date__lte=start + timedelta(days=45)).exclude(notes__icontains="demo").order_by("starts_at")
    return render(request, "trading/calendar.html", {"events": events})


@login_required
def user_list(request, discarded=False):
    states = InstrumentUserState.objects.filter(user=request.user, discarded=discarded, watched=not discarded).select_related("instrument").order_by("instrument__symbol")
    rows = _instrument_rows(request.user, Instrument.objects.filter(id__in=states.values("instrument_id")).prefetch_related("theme_links__theme"))
    return render(request, "trading/watchlist.html", {"rows": rows, "discarded": discarded})


@login_required
def instrument_detail(request, pk):
    instrument = get_object_or_404(Instrument.objects.prefetch_related("theme_links__theme", "financial_statements", "analyst_snapshots"), pk=pk, active=True)
    metric = _latest_metric(instrument)
    state, _ = InstrumentUserState.objects.get_or_create(user=request.user, instrument=instrument)
    bar = _latest_bar(instrument)
    analyst_query = instrument.analyst_snapshots.order_by("-observed_at")
    statements = instrument.financial_statements.all()
    analyst_query = analyst_query.exclude(provider="DEMO")
    statements = statements.exclude(source="DEMO")
    analyst = analyst_query.first()
    report = build_asset_report(instrument, metric=metric, bar=bar)
    valuation = (metric.fair_value or metric.target_mean) if metric else None
    return render(request, "trading/instrument_detail.html", {"instrument": instrument, "metric": metric, "state": state, "bar": bar, "analyst": analyst, "statements": statements, "report": report, "valuation": valuation, "valuation_label": "Valor justo" if metric and metric.fair_value is not None else "Objetivo analistas", "upside": fair_value_upside(bar.close if bar else None, valuation), "indicators": INDICATORS, "broker_choices": InstrumentUserState.BROKER_STATUS, "chart_url": reverse("trading:api_chart", args=[instrument.pk])})


@require_GET
@login_required
def api_search(request):
    query = request.GET.get("q", "").strip()
    items = Instrument.objects.filter(active=True).filter(Q(symbol__icontains=query) | Q(name__icontains=query))[:25] if query else Instrument.objects.filter(active=True)[:25]
    results = [{"id": item.id, "symbol": item.symbol, "name": item.name, "asset_type": item.asset_type, "local": True} for item in items]
    live_enabled = True
    if query and len(results) < 25:
        try:
            local_symbols = {item["symbol"].upper() for item in results}
            for candidate in YahooFinanceProvider().search_instruments(query):
                if candidate.symbol.upper() not in local_symbols:
                    results.append({"id": None, "symbol": candidate.symbol, "name": candidate.name, "asset_type": candidate.asset_type, "exchange": candidate.exchange, "local": False})
                if len(results) >= 25:
                    break
        except ProviderError:
            pass
    return JsonResponse({"results": results, "live_enabled": live_enabled})


@require_POST
@login_required
def api_import_symbol(request):
    symbol = request.POST.get("symbol", "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9.:-]{1,20}", symbol):
        return JsonResponse({"error": "Símbolo inválido."}, status=400)
    try:
        provider = TwelveDataProvider() if settings.TRADING_TWELVE_DATA_API_KEY else YahooFinanceProvider()
        instrument = sync_symbol(symbol, provider=provider)
    except (ProviderError, ValueError) as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    warnings = []
    if instrument.asset_type == "STOCK":
        try:
            sync_sec_instrument(instrument)
        except ProviderError as exc:
            warnings.append(f"Se guardó el precio, pero SEC no entregó fundamentales: {exc}")
    return JsonResponse({"id": instrument.id, "symbol": instrument.symbol, "detail_url": reverse("trading:instrument_detail", args=[instrument.pk]), "warnings": warnings}, status=201)


@require_GET
@login_required
def api_screener(request):
    rows = _instrument_rows(request.user, Instrument.objects.filter(active=True).prefetch_related("theme_links__theme"))
    return JsonResponse({"results": [{"id": row["instrument"].id, "symbol": row["instrument"].symbol, "name": row["instrument"].name, "price": row["price"], "change_pct": row["change"], "quant_score": float(row["metric"].quant_score) if row["metric"] and row["metric"].quant_score is not None else None, "upside_pct": row["upside"], "themes": [theme.name for theme in row["themes"]]} for row in rows]})


@require_GET
@login_required
def api_chart(request, pk, chart_key=None):
    instrument = get_object_or_404(Instrument, pk=pk, active=True)
    return JsonResponse(chart_payload(instrument, chart_key or request.GET.get("chart", "price"), request.GET.get("range", "1Y")))


@require_POST
@login_required
def api_state(request, pk):
    instrument = get_object_or_404(Instrument, pk=pk, active=True)
    state, _ = InstrumentUserState.objects.get_or_create(user=request.user, instrument=instrument)
    action = request.POST.get("action")
    if action == "watch":
        state.watched = not state.watched
    elif action == "discard":
        state.discarded = True
        state.watched = False
    elif action == "restore":
        state.discarded = False
    elif action == "broker_status" and request.POST.get("value") in dict(InstrumentUserState.BROKER_STATUS):
        state.broker_status = request.POST["value"]
    else:
        return JsonResponse({"error": "Acción inválida"}, status=400)
    state.save()
    payload = {"watched": state.watched, "discarded": state.discarded, "broker_status": state.broker_status}
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
        return JsonResponse(payload)
    return redirect(request.POST.get("next") or reverse("trading:instrument_detail", args=[instrument.pk]))


@require_POST
@login_required
def api_watch(request, pk):
    request.POST = request.POST.copy()
    request.POST["action"] = "watch"
    request.META["HTTP_X_REQUESTED_WITH"] = "XMLHttpRequest"
    return api_state(request, pk)


@require_POST
@login_required
def api_broker_status(request, pk):
    request.POST = request.POST.copy()
    request.POST["action"] = "broker_status"
    request.META["HTTP_X_REQUESTED_WITH"] = "XMLHttpRequest"
    return api_state(request, pk)


@require_POST
@login_required
def api_ai_research(request):
    prompt = request.POST.get("prompt", "").strip()
    if not prompt:
        return JsonResponse({"error": "Escribe una consulta de investigación."}, status=400)
    run = run_copilot(request.user, prompt)
    return JsonResponse({"id": run.id, "result": run.result, "normalized_filter": run.normalized_filter, "evidence": run.evidence})
