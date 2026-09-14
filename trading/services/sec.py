from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from trading.models import FinancialStatementSnapshot, InstrumentMetricSnapshot
from trading.services.live import _technical_metrics
from trading.services.providers.sec import SECProvider
from trading.services.scoring import calculate_quant_score


INCOME_TAGS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "eps": ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"),
    "dividend_per_share": ("CommonStockDividendsPerShareDeclared", "CommonStockDividendsPerShareCashPaid"),
}
BALANCE_TAGS = {
    "assets": ("Assets",),
    "cash": ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
    "equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
}
CASHFLOW_TAGS = {
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
}
DEBT_CURRENT_TAGS = ("LongTermDebtCurrent", "LongTermDebtAndFinanceLeaseObligationsCurrent")
DEBT_LONG_TAGS = ("LongTermDebtNoncurrent", "LongTermDebtAndFinanceLeaseObligationsNoncurrent")


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _annual_rows(facts, tags, unit="USD"):
    us_gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    for tag in tags:
        units = ((us_gaap.get(tag) or {}).get("units") or {})
        rows = units.get(unit) or []
        annual = [row for row in rows if row.get("form") in {"10-K", "10-K/A"} and row.get("fp") == "FY" and row.get("end")]
        if not annual:
            continue
        by_end = {}
        for row in annual:
            end = row["end"][:10]
            current = by_end.get(end)
            if current is None or str(row.get("filed", "")) > str(current.get("filed", "")):
                by_end[end] = row
        return sorted(by_end.values(), key=lambda row: row["end"])
    return []


def _latest_value(facts, tags, unit="USD"):
    rows = _annual_rows(facts, tags, unit=unit)
    return _decimal(rows[-1].get("val")) if rows else None


def _annual_series(facts, tags, unit="USD"):
    return {row["end"][:10]: row for row in _annual_rows(facts, tags, unit=unit)}


def _latest_shares(facts):
    rows = (((facts.get("facts") or {}).get("dei") or {}).get("EntityCommonStockSharesOutstanding") or {}).get("units", {}).get("shares", [])
    eligible = [row for row in rows if row.get("form") in {"10-K", "10-K/A", "10-Q", "10-Q/A"} and row.get("val") is not None]
    latest = max(eligible, key=lambda row: (str(row.get("filed", "")), str(row.get("end", ""))), default=None)
    return _decimal(latest.get("val")) if latest else None


def _growth(current, previous):
    current = _decimal(current)
    previous = _decimal(previous)
    if current is None or previous in (None, 0):
        return None
    return ((current / previous) - 1) * 100


def _ratio(numerator, denominator, multiplier=Decimal("100")):
    numerator = _decimal(numerator)
    denominator = _decimal(denominator)
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator * multiplier


def _latest_non_demo_price(instrument):
    bar = instrument.daily_bars.exclude(source="DEMO").order_by("-session_date").first()
    return (_decimal(bar.close), bar.session_date) if bar else (None, None)


def sync_sec_instrument(instrument, provider=None):
    """Persiste estados financieros oficiales SEC y métricas calculables, sin inventar faltantes."""
    provider = provider or SECProvider()
    facts = provider.fetch_company_facts(instrument.symbol)
    company = facts.get("resolved_company") or {}
    series = {key: _annual_series(facts, tags, unit="USD/shares" if key in {"eps", "dividend_per_share"} else "USD") for key, tags in INCOME_TAGS.items()}
    balance = {key: _annual_series(facts, tags) for key, tags in BALANCE_TAGS.items()}
    cashflow = {key: _annual_series(facts, tags) for key, tags in CASHFLOW_TAGS.items()}
    debt_current = _annual_series(facts, DEBT_CURRENT_TAGS)
    debt_long = _annual_series(facts, DEBT_LONG_TAGS)
    years = sorted(set().union(*(set(values) for values in [*series.values(), *balance.values(), *cashflow.values(), debt_current, debt_long])))
    statement_count = 0

    def value(mapping, end):
        return (mapping.get(end) or {}).get("val")

    with transaction.atomic():
        if company.get("name") and (not instrument.name or instrument.name == instrument.symbol):
            instrument.name = company["name"]
            instrument.save(update_fields=["name", "updated_at"])
        for end in years[-10:]:
            try:
                period_end = date.fromisoformat(end)
            except ValueError:
                continue
            income = {key: value(rows, end) for key, rows in series.items()}
            balance_data = {key: value(rows, end) for key, rows in balance.items()}
            cashflow_data = {key: value(rows, end) for key, rows in cashflow.items()}
            current_debt = _decimal(value(debt_current, end)) or Decimal("0")
            long_debt = _decimal(value(debt_long, end)) or Decimal("0")
            if current_debt or long_debt:
                balance_data["debt"] = current_debt + long_debt
            ocf = _decimal(cashflow_data.get("operating_cash_flow"))
            capex = _decimal(cashflow_data.get("capex"))
            if ocf is not None and capex is not None:
                cashflow_data["free_cash_flow"] = ocf - capex
            for statement_type, data in (("INCOME", income), ("BALANCE", balance_data), ("CASHFLOW", cashflow_data)):
                clean = {key: float(item) if isinstance(item, Decimal) else item for key, item in data.items() if item is not None}
                if not clean:
                    continue
                FinancialStatementSnapshot.objects.update_or_create(
                    instrument=instrument,
                    statement_type=statement_type,
                    period_end=period_end,
                    defaults={"fiscal_period": "FY", "data": clean, "source": provider.name},
                )
                statement_count += 1

        revenue_rows = series["revenue"]
        eps_rows = series["eps"]
        latest_end = max(years) if years else None
        prior_end = years[-2] if len(years) > 1 else None
        revenue = value(revenue_rows, latest_end) if latest_end else None
        operating_income = value(series["operating_income"], latest_end) if latest_end else None
        gross_profit = value(series["gross_profit"], latest_end) if latest_end else None
        net_income = value(series["net_income"], latest_end) if latest_end else None
        equity = value(balance["equity"], latest_end) if latest_end else None
        debt = (_decimal(value(debt_current, latest_end)) or 0) + (_decimal(value(debt_long, latest_end)) or 0) if latest_end else None
        eps = _decimal(value(eps_rows, latest_end)) if latest_end else None
        dividend = _decimal(value(series["dividend_per_share"], latest_end)) if latest_end else None
        price, price_date = _latest_non_demo_price(instrument)
        shares = _latest_shares(facts)

        values = {
            "market_cap": price * shares if price is not None and shares is not None else None,
            "pe": _ratio(price, eps, Decimal("1")) if eps and eps > 0 else None,
            "dividend_yield": _ratio(dividend, price),
            "revenue_growth": _growth(revenue, value(revenue_rows, prior_end)) if prior_end else None,
            "eps_growth": _growth(eps, value(eps_rows, prior_end)) if prior_end else None,
            "gross_margin": _ratio(gross_profit, revenue),
            "operating_margin": _ratio(operating_income, revenue),
            "roe": _ratio(net_income, equity),
            "debt_to_equity": _ratio(debt, equity, Decimal("1")),
        }
        technical = _technical_metrics(instrument)
        values.update({key: technical.get(key) for key in ("rsi14", "sma20", "sma50", "sma200", "volatility_30d")})
        score = calculate_quant_score({**{key: item for key, item in values.items() if item is not None}, **technical}, asset_type=instrument.asset_type)
        latest_fact = max(
            (row for rows in revenue_rows.values() for row in [rows]),
            key=lambda row: str(row.get("filed", "")),
            default={},
        )
        metadata = {
            "sec_cik": company.get("cik"),
            "source_url": f"https://www.sec.gov/edgar/browse/?CIK={company.get('cik', '')}",
            "market_source": instrument.daily_bars.exclude(source="DEMO").order_by("-session_date").values_list("source", flat=True).first(),
            "price_as_of": price_date.isoformat() if price_date else None,
            "financials_as_of": latest_end,
            "last_filed": latest_fact.get("filed"),
            "technical": technical,
            "quant_components": score["components"],
            "data_quality_note": "Precios de mercado públicos y estados financieros oficiales SEC EDGAR. Los campos sin fuente quedan vacíos; no son datos demo.",
        }
        if statement_count or price is not None:
            metric_fields = {field.name for field in InstrumentMetricSnapshot._meta.fields}
            defaults = {key: item for key, item in values.items() if key in metric_fields and item is not None}
            defaults.update({"metrics": metadata, "quant_score": score["score"], "quant_coverage": score["coverage"], "source": provider.name})
            InstrumentMetricSnapshot.objects.create(instrument=instrument, observed_at=timezone.now(), **defaults)

    return {"updated": 1 if statement_count or price is not None else 0, "statements": statement_count, "warnings": []}
