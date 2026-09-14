from __future__ import annotations

import re

from django.db.models import Q

from trading.models import AIResearchRun, Instrument


THEME_ALIASES = {
    "energia": ("energía", "energia", "petróleo", "petroleo"),
    "ia-y-semiconductores": ("inteligencia artificial", "semiconductor", "chips", " ia "),
    "dividendos": ("dividendo", "dividendos"),
    "tecnologia": ("tecnología", "tecnologia", "software"),
}


def _threshold(text, pattern):
    match = re.search(pattern, text)
    return float(match.group(1).replace(",", ".")) if match else None


def parse_prompt(prompt: str) -> dict:
    raw = (prompt or "")[:1000]
    text = f" {raw.lower()} "
    symbols = list(
        Instrument.objects.filter(active=True)
        .filter(Q(symbol__in=re.findall(r"\b[A-Za-z][A-Za-z0-9.-]{0,14}\b", raw.upper())))
        .values_list("symbol", flat=True)
    )
    theme = next((slug for slug, aliases in THEME_ALIASES.items() if any(alias in text for alias in aliases)), "")
    asset_type = "CRYPTO" if "cripto" in text or "crypto" in text else "ETF" if re.search(r"\betfs?\b", text) else "STOCK" if any(word in text for word in ("empresa", "acción", "accion")) else ""
    return {
        "query": raw.strip(),
        "symbols": symbols,
        "theme": theme,
        "asset_type": asset_type,
        "min_quant": _threshold(text, r"quant(?:pro)?[^\d]{0,20}(?:mayor|sobre|>|mínimo|minimo)\s*(\d+(?:[.,]\d+)?)"),
        "max_pe": _threshold(text, r"(?:p/?e|per)[^\d]{0,20}(?:menor|bajo|<|máximo|maximo)\s*(\d+(?:[.,]\d+)?)"),
        "min_dividend": _threshold(text, r"dividend[^\d]{0,20}(?:mayor|sobre|>|mínimo|minimo)\s*(\d+(?:[.,]\d+)?)"),
        "sort": "valuation" if any(word in text for word in ("subvalorad", "barat", "valoración", "valoracion")) else "quant",
    }


def run_copilot(user, prompt: str) -> AIResearchRun:
    normalized = parse_prompt(prompt)
    queryset = Instrument.objects.filter(active=True)
    if normalized["symbols"]:
        queryset = queryset.filter(symbol__in=normalized["symbols"])
    if normalized["asset_type"]:
        queryset = queryset.filter(asset_type=normalized["asset_type"])
    if normalized["theme"]:
        queryset = queryset.filter(theme_links__theme__slug__icontains=normalized["theme"])
    if not normalized["symbols"] and not normalized["theme"]:
        words = [word for word in re.findall(r"[\wáéíóúñ.-]+", normalized["query"], flags=re.IGNORECASE) if len(word) >= 3]
        meaningful = [word for word in words if word.lower() not in {"busca", "buscar", "quiero", "analiza", "analizar", "empresa", "empresas", "acción", "acciones", "sobre", "para", "con", "del", "los", "las"}]
        if meaningful:
            query_filter = Q()
            for word in meaningful[:5]:
                query_filter |= Q(symbol__iexact=word) | Q(name__icontains=word) | Q(sector__icontains=word) | Q(industry__icontains=word)
            matched = queryset.filter(query_filter)
            if matched.exists():
                queryset = matched

    rows = []
    evidence = []
    for instrument in queryset.prefetch_related("theme_links__theme")[:150]:
        metric = instrument.metric_snapshots.exclude(source="DEMO").order_by("-observed_at").first()
        bars = instrument.daily_bars.exclude(source="DEMO").order_by("-session_date")[:2]
        if not bars:
            continue
        bar = bars[0]
        price = float(bar.close)
        change = ((price / float(bars[1].close)) - 1) * 100 if len(bars) > 1 and bars[1].close else None
        quant = float(metric.quant_score) if metric and metric.quant_score is not None else None
        pe = float(metric.pe) if metric and metric.pe is not None else None
        dividend = float(metric.dividend_yield) if metric and metric.dividend_yield is not None else None
        if normalized["min_quant"] is not None and (quant is None or quant < normalized["min_quant"]):
            continue
        if normalized["max_pe"] is not None and (pe is None or pe > normalized["max_pe"]):
            continue
        if normalized["min_dividend"] is not None and (dividend is None or dividend < normalized["min_dividend"]):
            continue
        reasons = [f"cotización disponible al {bar.session_date.isoformat()}"]
        if quant is not None:
            reasons.append(f"QuantPro {quant:.1f} con cobertura {float(metric.quant_coverage or 0):.0f}%")
        if pe is not None:
            reasons.append(f"P/E {pe:.2f}")
        rows.append({
            "id": instrument.id,
            "symbol": instrument.symbol,
            "name": instrument.name,
            "price": price,
            "change_pct": round(change, 2) if change is not None else None,
            "pe": pe,
            "dividend_yield": dividend,
            "quant_score": quant,
            "themes": [link.theme.name for link in instrument.theme_links.all()],
            "source": bar.source,
            "as_of": bar.session_date.isoformat(),
            "why": "; ".join(reasons),
        })
        evidence.append({"type": "market_close", "instrument": instrument.symbol, "source": bar.source, "as_of": bar.session_date.isoformat()})
        if metric:
            evidence.append({"type": "metric_snapshot", "instrument": instrument.symbol, "source": metric.source, "as_of": metric.observed_at.isoformat()})

    if normalized["sort"] == "valuation":
        rows.sort(key=lambda row: row["pe"] if row["pe"] is not None else 10**9)
    else:
        rows.sort(key=lambda row: row["quant_score"] if row["quant_score"] is not None else -1, reverse=True)
    rows = rows[:20]
    if rows:
        summary = f"Encontré {len(rows)} activos con información actual que coinciden con la consulta."
        unknowns = ["Los campos sin fuente verificable se muestran vacíos."]
    else:
        summary = "No encontré activos con datos actuales que cumplan esa consulta. No usaré datos demo para completar el resultado."
        unknowns = ["Prueba con un símbolo como MCD o pulsa “Actualizar información ahora” para refrescar las fuentes."]
    result = {
        "summary": summary,
        "assets": rows,
        "limits": ["Este asistente organiza evidencia para investigar; no recomienda ni ejecuta operaciones."],
        "unknowns": unknowns,
    }
    return AIResearchRun.objects.create(
        user=user,
        prompt=prompt[:4000],
        normalized_filter=normalized,
        result=result,
        evidence=evidence[:40],
        model_name="evidence-research-filter-v2",
    )
