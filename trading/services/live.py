from __future__ import annotations

import math
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from statistics import pstdev

from django.db import transaction
from django.utils import timezone

from trading.models import AnalystSnapshot, FinancialStatementSnapshot, InstrumentMetricSnapshot, MarketEvent
from trading.services.providers.fmp import FMPProvider
from trading.services.providers.twelve_data import ProviderError
from trading.services.scoring import calculate_quant_score


def _decimal(value):
    if value in (None, "", "None"):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _percent(value):
    number = _decimal(value)
    if number is None:
        return None
    return number * 100 if abs(number) <= 2 else number


def _first(payload):
    if isinstance(payload, list):
        return payload[0] if payload else {}
    return payload if isinstance(payload, dict) else {}


def _has_payload(value):
    if isinstance(value, dict):
        return any(_has_payload(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_payload(item) for item in value)
    return value not in (None, "")


def _period_end(row):
    raw = row.get("date") or row.get("calendarYear") or row.get("fiscalYear")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10]) if "-" in str(raw) else date(int(raw), 12, 31)
    except (TypeError, ValueError):
        return None


def _canonical_statement(statement_type, row):
    if statement_type == "INCOME":
        return {
            "revenue": row.get("revenue"),
            "ebitda": row.get("ebitda"),
            "net_income": row.get("netIncome"),
            "eps": row.get("epsDiluted", row.get("eps")),
            "gross_margin": row.get("grossProfitRatio"),
            "operating_margin": row.get("operatingIncomeRatio"),
            "provider_payload": row,
        }
    if statement_type == "BALANCE":
        return {
            "assets": row.get("totalAssets"),
            "cash": row.get("cashAndCashEquivalents"),
            "debt": row.get("totalDebt"),
            "equity": row.get("totalStockholdersEquity", row.get("totalEquity")),
            "shares": row.get("commonStock", row.get("weightedAverageShsOut")),
            "provider_payload": row,
        }
    return {
        "operating_cash_flow": row.get("operatingCashFlow"),
        "free_cash_flow": row.get("freeCashFlow"),
        "capex": row.get("capitalExpenditure"),
        "provider_payload": row,
    }


def _technical_metrics(instrument):
    closes = [
        float(item.adjusted_close or item.close)
        for item in instrument.daily_bars.exclude(source="DEMO").order_by("session_date")
    ]
    if not closes:
        return {}
    current = closes[-1]
    result = {"close": current}
    for days in (20, 50, 200):
        if len(closes) >= days:
            average = sum(closes[-days:]) / days
            result[f"sma{days}"] = average
            result[f"sma{days}_vs_close"] = ((current / average) - 1) * 100 if average else None
    if len(closes) >= 15:
        changes = [closes[index] - closes[index - 1] for index in range(len(closes) - 14, len(closes))]
        gains = sum(max(change, 0) for change in changes) / 14
        losses = sum(max(-change, 0) for change in changes) / 14
        result["rsi14"] = 100 if not losses else 100 - (100 / (1 + gains / losses))
    if len(closes) >= 31:
        returns = [(closes[index] / closes[index - 1]) - 1 for index in range(len(closes) - 29, len(closes)) if closes[index - 1]]
        result["volatility_30d"] = pstdev(returns) * math.sqrt(252) * 100 if len(returns) > 1 else None
    if len(closes) >= 2:
        start = closes[-252] if len(closes) >= 252 else closes[0]
        result["one_year_return"] = ((current / start) - 1) * 100 if start else None
        peak = closes[0]
        max_drawdown = 0.0
        for value in closes:
            peak = max(peak, value)
            max_drawdown = min(max_drawdown, (value / peak) - 1 if peak else 0)
        result["max_drawdown"] = max_drawdown * 100
    return result


def _metric_values(ratios, profile, technical, analyst):
    grades = _first(analyst.get("grades"))
    targets = _first(analyst.get("targets"))
    buy = (grades.get("strongBuy") or 0) + (grades.get("buy") or 0)
    sell = (grades.get("strongSell") or 0) + (grades.get("sell") or 0)
    return {
        "market_cap": _decimal(profile.get("marketCap")),
        "pe": _decimal(ratios.get("priceToEarningsRatioTTM", ratios.get("peRatioTTM"))),
        "ps": _decimal(ratios.get("priceToSalesRatioTTM")),
        "pb": _decimal(ratios.get("priceToBookRatioTTM")),
        "ev_ebitda": _decimal(ratios.get("enterpriseValueMultipleTTM")),
        "dividend_yield": _percent(ratios.get("dividendYieldTTM")),
        "gross_margin": _percent(ratios.get("grossProfitMarginTTM")),
        "operating_margin": _percent(ratios.get("operatingProfitMarginTTM")),
        "roe": _percent(ratios.get("returnOnEquityTTM")),
        "debt_to_equity": _decimal(ratios.get("debtToEquityRatioTTM")),
        "beta": _decimal(profile.get("beta")),
        "rsi14": _decimal(technical.get("rsi14")),
        "sma20": _decimal(technical.get("sma20")),
        "sma50": _decimal(technical.get("sma50")),
        "sma200": _decimal(technical.get("sma200")),
        "target_mean": _decimal(targets.get("targetConsensus", targets.get("lastYearAvgPriceTarget"))),
        "target_high": _decimal(targets.get("targetHigh")),
        "target_low": _decimal(targets.get("targetLow")),
        "analyst_buy": int(buy) if buy else None,
        "analyst_hold": int(grades.get("hold")) if grades.get("hold") is not None else None,
        "analyst_sell": int(sell) if sell else None,
        "volatility_30d": _decimal(technical.get("volatility_30d")),
    }


def _growth_metrics(income_rows):
    ordered = sorted((row for row in income_rows if _period_end(row)), key=_period_end, reverse=True)
    if len(ordered) < 2:
        return {}
    latest, previous = ordered[:2]
    result = {}
    for field, output in (("revenue", "revenue_growth"), ("epsDiluted", "eps_growth")):
        current = _decimal(latest.get(field, latest.get("eps") if field == "epsDiluted" else None))
        prior = _decimal(previous.get(field, previous.get("eps") if field == "epsDiluted" else None))
        if current is not None and prior not in (None, 0):
            result[output] = ((current / prior) - 1) * 100
    return result


def sync_fmp_instrument(instrument, provider=None):
    """Persiste perfil, estados, métricas y consenso reales sin rellenar faltantes."""
    provider = provider or FMPProvider()
    payloads = {"profile": {}, "ratios": {}, "statements": {}, "analyst": {}}
    warnings = []
    for key, loader in (
        ("profile", lambda: provider.fetch_profile(instrument.symbol)),
        ("ratios", lambda: provider.fetch_metrics(instrument.symbol)),
        ("statements", lambda: provider.fetch_financial_statements(instrument.symbol)),
        ("analyst", lambda: provider.fetch_analyst_data(instrument.symbol)),
    ):
        try:
            payloads[key] = loader() or {}
        except ProviderError as exc:
            warnings.append(f"{key}: {exc}")

    profile = _first(payloads["profile"])
    ratios = _first(payloads["ratios"])
    statements = payloads["statements"] if isinstance(payloads["statements"], dict) else {}
    analyst = payloads["analyst"] if isinstance(payloads["analyst"], dict) else {}
    if not any(_has_payload(item) for item in (profile, ratios, statements, analyst)):
        return {"updated": 0, "statements": 0, "warnings": warnings or ["FMP no devolvió datos."]}

    with transaction.atomic():
        profile_fields = {
            "name": profile.get("companyName"),
            "exchange": profile.get("exchangeShortName", profile.get("exchange")),
            "currency": profile.get("currency"),
            "country": profile.get("country"),
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
            "description": profile.get("description"),
        }
        changed = []
        for field, value in profile_fields.items():
            if value and getattr(instrument, field) != value:
                setattr(instrument, field, value)
                changed.append(field)
        if instrument.source != provider.name:
            instrument.source = provider.name
            changed.append("source")
        if changed:
            instrument.save(update_fields=[*changed, "updated_at"])

        statement_count = 0
        statement_types = {"income": "INCOME", "balance": "BALANCE", "cashflow": "CASHFLOW"}
        for key, statement_type in statement_types.items():
            for row in statements.get(key, []) or []:
                period_end = _period_end(row)
                if not period_end:
                    continue
                FinancialStatementSnapshot.objects.update_or_create(
                    instrument=instrument,
                    statement_type=statement_type,
                    period_end=period_end,
                    defaults={"fiscal_period": row.get("period", "FY"), "data": _canonical_statement(statement_type, row), "source": provider.name},
                )
                statement_count += 1

        estimates = analyst.get("estimates", []) or []
        grades = _first(analyst.get("grades"))
        targets = _first(analyst.get("targets"))
        eps_estimates = []
        for row in estimates:
            period_end = _period_end(row)
            value = row.get("estimatedEpsAvg", row.get("epsAvg"))
            if period_end and value is not None:
                eps_estimates.append({"year": period_end.year, "value": float(value)})

        technical = _technical_metrics(instrument)
        values = _metric_values(ratios, profile, technical, analyst)
        income_rows = statements.get("income", []) or []
        values.update(_growth_metrics(income_rows))
        score_input = {**{key: value for key, value in values.items() if value is not None}, **technical}
        score = calculate_quant_score(score_input, asset_type=instrument.asset_type)
        metadata = {
            **ratios,
            "headquarters": ", ".join(part for part in (profile.get("city"), profile.get("state"), profile.get("country")) if part),
            "business_moat": profile.get("description") or instrument.description,
            "profile_facts": [
                {"label": "Sector", "value": profile.get("sector")},
                {"label": "Industria", "value": profile.get("industry")},
                {"label": "Empleados", "value": profile.get("fullTimeEmployees")},
            ],
            "eps_estimates": eps_estimates,
            "technical": technical,
            "quant_components": score["components"],
            "data_quality_note": "Datos reales de FMP y precios persistidos del proveedor de mercado; revisa fuente, fecha y cobertura de cada bloque.",
        }
        metadata["profile_facts"] = [item for item in metadata["profile_facts"] if item["value"] not in (None, "")]
        metric_defaults = {key: value for key, value in values.items() if key in {field.name for field in InstrumentMetricSnapshot._meta.fields}}
        metric_defaults.update({"metrics": metadata, "quant_score": score["score"], "quant_coverage": score["coverage"], "source": provider.name})
        InstrumentMetricSnapshot.objects.create(instrument=instrument, observed_at=timezone.now(), **metric_defaults)

        if grades or targets:
            buy = int((grades.get("strongBuy") or 0) + (grades.get("buy") or 0))
            sell = int((grades.get("strongSell") or 0) + (grades.get("sell") or 0))
            hold = int(grades.get("hold") or 0)
            consensus = "BUY" if buy > max(hold, sell) else "SELL" if sell > max(buy, hold) else "HOLD"
            AnalystSnapshot.objects.create(
                instrument=instrument,
                observed_at=timezone.now(),
                provider=provider.name,
                consensus=consensus,
                target_mean=values.get("target_mean"),
                target_high=values.get("target_high"),
                target_low=values.get("target_low"),
                ratings={"grades": grades, "estimates": estimates},
                source_url=f"{provider.base_url}/grades-consensus?symbol={instrument.symbol}",
            )
    return {"updated": 1, "statements": statement_count, "warnings": warnings}


def sync_fmp_events(provider=None, start=None, end=None):
    provider = provider or FMPProvider()
    start = start or timezone.localdate()
    end = end or start
    events = provider.fetch_events(start, end)
    updated = 0
    for row in events or []:
        raw_date = row.get("date")
        if not raw_date:
            continue
        try:
            starts_at = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00"))
        except ValueError:
            continue
        if starts_at.tzinfo is None:
            starts_at = timezone.make_aware(datetime.combine(starts_at.date(), time(hour=8)))
        symbol = row.get("symbol", "Mercado")
        MarketEvent.objects.update_or_create(
            title=f"{symbol} · Resultados",
            starts_at=starts_at,
            defaults={
                "event_type": "EARNINGS",
                "country": "US",
                "impact": "MEDIUM",
                "actual": str(row.get("epsActual") or row.get("revenueActual") or ""),
                "forecast": str(row.get("epsEstimated") or row.get("revenueEstimated") or ""),
                "previous": "",
                "source_url": f"{provider.base_url}/earnings-calendar",
                "notes": "Calendario de resultados sincronizado desde FMP.",
            },
        )
        updated += 1
    return updated
