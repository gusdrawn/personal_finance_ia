from __future__ import annotations

from decimal import Decimal
from typing import Any


def _percentile(value: float, values: list[float], reverse: bool = False) -> float | None:
    if value is None or not values:
        return None
    ordered = sorted(values)
    rank = sum(1 for item in ordered if item <= value) / len(ordered) * 100
    return round(100 - rank if reverse else rank, 2)


def calculate_quant_score(metrics: dict[str, Any], universe_metrics: list[dict[str, Any]] | None = None, asset_type: str = "STOCK") -> dict[str, Any]:
    """Calcula un score auditable; los datos faltantes son UNKNOWN, no cero."""
    universe_metrics = universe_metrics or []
    if asset_type == "STOCK":
        weights = {"value": 0.25, "quality": 0.25, "growth": 0.20, "momentum": 0.20, "risk": 0.10}
        components = {
            "value": _component(metrics, ("pe", "ps", "pb", "ev_ebitda"), reverse=True),
            "quality": _component(metrics, ("roe", "gross_margin", "operating_margin")),
            "growth": _component(metrics, ("revenue_growth", "eps_growth")),
            "momentum": _component(metrics, ("rsi14", "sma20_vs_close", "sma50_vs_close")),
            "risk": _component(metrics, ("volatility_30d", "debt_to_equity", "short_float"), reverse=True),
        }
    elif asset_type == "ETF":
        weights = {"momentum": 0.35, "trend": 0.25, "risk": 0.20, "liquidity": 0.20}
        components = {key: _component(metrics, fields, reverse=key == "risk") for key, fields in {
            "momentum": ("rsi14", "one_year_return"), "trend": ("sma50_vs_close", "sma200_vs_close"),
            "risk": ("volatility_30d", "max_drawdown"), "liquidity": ("volume", "market_cap"),
        }.items()}
    else:
        weights = {"momentum": 0.40, "trend": 0.25, "risk": 0.20, "liquidity": 0.15}
        components = {key: _component(metrics, fields, reverse=key == "risk") for key, fields in {
            "momentum": ("rsi14", "one_year_return"), "trend": ("sma50_vs_close", "sma200_vs_close"),
            "risk": ("volatility_30d", "max_drawdown"), "liquidity": ("volume", "market_cap"),
        }.items()}
    known = {key: value for key, value in components.items() if value is not None}
    coverage = sum(weights[key] for key in known) / sum(weights.values()) * 100 if weights else 0
    score = sum(known[key] * weights[key] for key in known) / sum(weights[key] for key in known) if known else None
    return {
        "score": round(score, 2) if score is not None and coverage >= 60 else None,
        "coverage": round(coverage, 2),
        "components": {key: (round(value, 2) if value is not None else None) for key, value in components.items()},
        "classification": "FUERTE" if score is not None and score >= 75 else "VIGILAR" if score is not None and score >= 55 else "SIN_DATOS",
        "policy_version": "2026-08-trading-v1",
    }


def _component(metrics: dict[str, Any], fields: tuple[str, ...], reverse: bool = False) -> float | None:
    values = []
    for field in fields:
        value = metrics.get(field)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        # Map common ratios/percentages to an interpretable 0–100 signal.
        if field in {"pe", "ps", "pb", "ev_ebitda", "debt_to_equity", "volatility_30d", "short_float"}:
            signal = max(0.0, min(100.0, 100.0 - numeric * (3 if field in {"pe", "ps", "pb"} else 1)))
        elif field == "rsi14":
            signal = max(0.0, min(100.0, 100.0 - abs(50 - numeric)))
        elif field in {"sma20_vs_close", "sma50_vs_close", "sma200_vs_close", "one_year_return", "revenue_growth", "eps_growth"}:
            signal = max(0.0, min(100.0, 50.0 + numeric))
        else:
            signal = max(0.0, min(100.0, numeric))
        values.append(100 - signal if reverse else signal)
    return sum(values) / len(values) if values else None


def fair_value_upside(price: Decimal | float | None, fair_value: Decimal | float | None) -> float | None:
    if price in (None, 0) or fair_value is None:
        return None
    return round((float(fair_value) / float(price) - 1) * 100, 2)
