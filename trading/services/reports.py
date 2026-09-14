from __future__ import annotations

from typing import Any


_UNSET = object()


def _number(value):
    return float(value) if value is not None else None


def _compact_money(value) -> str:
    if value is None:
        return "—"
    number = float(value)
    for divisor, suffix in ((1_000_000_000_000, "T"), (1_000_000_000, "B"), (1_000_000, "M")):
        if abs(number) >= divisor:
            return f"${number / divisor:.1f}{suffix}"
    return f"${number:,.0f}"


def build_asset_report(instrument, metric=_UNSET, bar=_UNSET) -> dict[str, Any]:
    """Construye un reporte serializable desde snapshots ya persistidos."""
    metric_was_supplied = metric is not _UNSET
    bar_was_supplied = bar is not _UNSET
    if not metric_was_supplied:
        metric = instrument.metric_snapshots.order_by("-observed_at").first()
    if not bar_was_supplied:
        bar = instrument.daily_bars.order_by("-session_date").first()
    no_explicit_data = metric_was_supplied and bar_was_supplied and metric is None and bar is None
    primary_source = "SIN_DATOS" if no_explicit_data else metric.source if metric else bar.source if bar else instrument.source
    prefer_real = primary_source != "DEMO"
    if prefer_real and bar and bar.source == "DEMO":
        bar = None
    metadata = metric.metrics if metric else {}
    bars_query = instrument.daily_bars.order_by("session_date")
    if prefer_real:
        bars_query = bars_query.exclude(source="DEMO")
    bars = list(bars_query)
    price_points = [{"x": item.session_date.isoformat(), "y": _number(item.adjusted_close or item.close)} for item in bars]
    prices = [point["y"] for point in price_points if point["y"] is not None]
    one_year_return = ((prices[-1] / prices[0]) - 1) * 100 if len(prices) >= 2 and prices[0] else None
    peak = max(prices) if prices else None
    drawdown = ((prices[-1] / peak) - 1) * 100 if prices and peak else None

    income_query = instrument.financial_statements.filter(statement_type="INCOME").order_by("period_end")
    if prefer_real:
        income_query = income_query.exclude(source="DEMO")
    income = list(income_query)
    revenue = [{"year": item.period_end.year, "value": item.data.get("revenue")} for item in income if item.data.get("revenue") is not None]
    eps_actual = [{"year": item.period_end.year, "value": item.data.get("eps"), "estimate": False} for item in income if item.data.get("eps") is not None]
    eps_estimates = [{**item, "estimate": True} for item in metadata.get("eps_estimates", [])]
    year_end_prices = {}
    for item in bars:
        year_end_prices[item.session_date.year] = _number(item.adjusted_close or item.close)
    dividend_history = []
    pe_history = []
    for item in income:
        price = year_end_prices.get(item.period_end.year)
        dividend = _number(item.data.get("dividend_per_share"))
        eps = _number(item.data.get("eps"))
        if price and dividend is not None:
            dividend_history.append({"year": item.period_end.year, "value": round(dividend / price * 100, 2)})
        if price and eps and eps > 0:
            pe_history.append({"year": item.period_end.year, "value": round(price / eps, 2)})

    return {
        "identity": {
            "symbol": instrument.symbol,
            "name": instrument.name,
            "description": instrument.description,
            "industry": instrument.industry or "—",
            "headquarters": metadata.get("headquarters", instrument.country or "—"),
            "source": primary_source,
            "as_of": metric.observed_at.date().isoformat() if metric else (bar.session_date.isoformat() if bar else None),
        },
        "headline": {
            "price": _number(bar.close) if bar else None,
            "currency": instrument.currency,
            "market_cap": _number(metric.market_cap) if metric else None,
            "market_cap_display": _compact_money(metric.market_cap if metric else None),
            "dividend_yield": _number(metric.dividend_yield) if metric else None,
            "pe": _number(metric.pe) if metric else None,
            "quant_score": _number(metric.quant_score) if metric else None,
            "one_year_return": round(one_year_return, 2) if one_year_return is not None else None,
            "drawdown": round(drawdown, 2) if drawdown is not None else None,
        },
        "business": {
            "moat": metadata.get("business_moat", instrument.description or "Perfil empresarial pendiente de proveedor."),
            "facts": metadata.get("profile_facts", []),
        },
        "charts": {
            "price": price_points,
            "revenue": revenue,
            "dividend_yield": metadata.get("dividend_yield_history") or dividend_history,
            "pe": metadata.get("pe_history") or pe_history,
            "eps": eps_actual + eps_estimates,
        },
        "quality": {
            "note": (
                "No hay snapshots reales sincronizados para este activo."
                if no_explicit_data
                else metadata.get("data_quality_note", "Revisa la fuente y fecha de cada snapshot antes de usarlo.")
            ),
            "is_demo": primary_source == "DEMO",
            "sources": sorted({primary_source, *[item.source for item in bars], *[item.source for item in income]}),
        },
    }
