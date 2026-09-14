from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.utils import timezone
from django.utils.text import slugify

from trading.models import (
    AnalystSnapshot, DailyBar, FinancialStatementSnapshot, Instrument, InstrumentMetricSnapshot,
    InstrumentTheme, MarketEvent, Strategy, StrategyConstituent, StrategyReturn, Theme,
)


DEMO_INSTRUMENTS = [
    ("AAPL", "Apple Inc.", "STOCK", "Technology", "Consumer Electronics", "AI y semiconductores"),
    ("MSFT", "Microsoft Corp.", "STOCK", "Technology", "Software—Infrastructure", "IA y semiconductores"),
    ("NVDA", "NVIDIA Corp.", "STOCK", "Technology", "Semiconductors", "IA y semiconductores"),
    ("AMD", "Advanced Micro Devices", "STOCK", "Technology", "Semiconductors", "IA y semiconductores"),
    ("TSLA", "Tesla Inc.", "STOCK", "Consumer Cyclical", "Auto Manufacturers", "Energía limpia"),
    ("XOM", "Exxon Mobil Corp.", "STOCK", "Energy", "Oil & Gas Integrated", "Energía"),
    ("CVX", "Chevron Corp.", "STOCK", "Energy", "Oil & Gas Integrated", "Energía"),
    ("JPM", "JPMorgan Chase & Co.", "STOCK", "Financial Services", "Banks—Diversified", "Dividendos"),
    ("V", "Visa Inc.", "STOCK", "Financial Services", "Credit Services", "Calidad"),
    ("MCD", "McDonald's Corporation", "STOCK", "Consumer Cyclical", "Restaurants", "Dividendos"),
    ("QQQ", "Invesco QQQ Trust", "ETF", "Broad Market", "Large Cap Growth", "Tecnología"),
    ("SPY", "SPDR S&P 500 ETF Trust", "ETF", "Broad Market", "Large Cap Blend", "Índices"),
    ("BTCUSD", "Bitcoin", "CRYPTO", "Crypto", "Layer 1", "Activos digitales"),
    ("ETHUSD", "Ethereum", "CRYPTO", "Crypto", "Layer 1", "Activos digitales"),
    ("GLD", "SPDR Gold Shares", "COMMODITY", "Commodities", "Gold", "Materias primas"),
    ("USO", "United States Oil Fund", "COMMODITY", "Commodities", "Crude Oil", "Energía"),
]


MCD_REVENUE = {
    2021: 23_200_000_000,
    2022: 23_200_000_000,
    2023: 25_000_000_000,
    2024: 25_900_000_000,
    2025: 26_400_000_000,
}
MCD_EPS = {2021: 9.28, 2022: 10.10, 2023: 11.94, 2024: 11.72, 2025: 12.20}


def seed_demo_data(days: int = 320) -> dict[str, int]:
    themes = {}
    for name in {row[5] for row in DEMO_INSTRUMENTS} | {"Subvaloradas", "Momentum", "GARP", "Salud", "Defensa"}:
        slug = slugify(name)
        themes[name], created = Theme.objects.get_or_create(name=name, defaults={"slug": slug, "description": f"Temática {name} para investigación."})
        if not created and themes[name].slug != slug and not Theme.objects.filter(slug=slug).exclude(pk=themes[name].pk).exists():
            themes[name].slug = slug
            themes[name].save(update_fields=["slug"])
    instruments = []
    today = date.today()
    for index, (symbol, name, asset_type, sector, industry, theme_name) in enumerate(DEMO_INSTRUMENTS):
        instrument_defaults = {"name": name, "asset_type": asset_type, "sector": sector, "industry": industry, "currency": "USD", "source": "DEMO", "active": True}
        if symbol == "MCD":
            instrument_defaults.update({"country": "United States", "description": "Operador global de restaurantes con un modelo basado en franquicias, marca y activos inmobiliarios. Perfil demostrativo: conecta proveedores para cifras de mercado vigentes."})
        instrument, _ = Instrument.objects.update_or_create(symbol=symbol, exchange="DEMO", defaults=instrument_defaults)
        instruments.append(instrument)
        InstrumentTheme.objects.get_or_create(instrument=instrument, theme=themes[theme_name], defaults={"confidence": 100, "source": "DEMO"})
        if symbol in {"AAPL", "MSFT", "NVDA", "AMD", "TSLA", "XOM", "CVX", "JPM", "V"}:
            InstrumentTheme.objects.get_or_create(instrument=instrument, theme=themes["Subvaloradas" if index % 3 == 0 else "Momentum"], defaults={"confidence": 72, "source": "DEMO"})
        _seed_bars(instrument, days=days, seed=index + 5, today=today)
        latest = instrument.daily_bars.order_by("-session_date").first()
        price = float(latest.close) if latest else 100
        metrics = _metrics_for(index, price, symbol=symbol)
        observed_at = timezone.make_aware(datetime.combine(today, datetime.min.time()))
        InstrumentMetricSnapshot.objects.update_or_create(instrument=instrument, observed_at=observed_at, defaults={**metrics, "source": "DEMO"})
        AnalystSnapshot.objects.update_or_create(instrument=instrument, observed_at=observed_at, defaults={"provider": "DEMO", "consensus": "BUY" if index % 4 else "HOLD", "target_mean": metrics["target_mean"], "target_high": metrics["target_high"], "target_low": metrics["target_low"], "ratings": {"buy": 8 - index % 3, "hold": 4 + index % 2, "sell": index % 3}})
        if asset_type == "STOCK":
            statement_years = range(2021, 2026) if symbol == "MCD" else (today.year - 1,)
            for statement_year in statement_years:
                for statement_type in ("INCOME", "BALANCE", "CASHFLOW"):
                    FinancialStatementSnapshot.objects.update_or_create(instrument=instrument, statement_type=statement_type, period_end=date(statement_year, 12, 31), defaults={"fiscal_period": "FY", "data": _financial_data(index, statement_type, symbol=symbol, statement_year=statement_year), "source": "DEMO"})
    _seed_events(today)
    _seed_strategies(instruments, today)
    return {"instruments": len(instruments), "bars": DailyBar.objects.filter(instrument__in=instruments).count(), "events": MarketEvent.objects.filter(starts_at__date__gte=today).count()}


def _seed_bars(instrument, days: int, seed: int, today: date) -> None:
    rng = random.Random(seed)
    base = 35 + seed * 18
    # Keep seed data deterministic, idempotent and useful for chart demos.
    previous = base
    rows = []
    for offset in range(days, 0, -1):
        session = today - timedelta(days=offset)
        if session.weekday() >= 5:
            continue
        drift = (seed % 5 - 1) * 0.0009 + math.sin(offset / 17 + seed) * 0.0015
        close = max(2, previous * (1 + drift + rng.uniform(-0.018, 0.018)))
        open_price = previous * (1 + rng.uniform(-0.006, 0.006))
        high = max(open_price, close) * (1 + rng.uniform(0.001, 0.012))
        low = min(open_price, close) * (1 - rng.uniform(0.001, 0.012))
        rows.append(DailyBar(instrument=instrument, session_date=session, open=Decimal(f"{open_price:.6f}"), high=Decimal(f"{high:.6f}"), low=Decimal(f"{low:.6f}"), close=Decimal(f"{close:.6f}"), adjusted_close=Decimal(f"{close:.6f}"), volume=Decimal(str(rng.randint(250000, 8500000))), source="DEMO"))
        previous = close
    DailyBar.objects.bulk_create(rows, ignore_conflicts=True)


def _metrics_for(index: int, price: float, symbol: str = "") -> dict:
    pe = 12 + (index * 4.1) % 42
    fair = price * (0.82 + (index % 7) * 0.09)
    result = {
        "market_cap": Decimal(str(round(price * (1.2e9 + index * 2.1e8), 2))), "pe": Decimal(str(round(pe, 4))), "ps": Decimal(str(round(2.1 + index * .37, 4))), "pb": Decimal(str(round(1.5 + index * .22, 4))), "ev_ebitda": Decimal(str(round(8 + index * 1.7, 4))), "dividend_yield": Decimal(str(round(max(0, 4.2 - index * .21), 4))), "revenue_growth": Decimal(str(round(5 + (index % 8) * 4.5, 4))), "eps_growth": Decimal(str(round(4 + (index % 7) * 5.2, 4))), "gross_margin": Decimal(str(round(30 + index * 2.2, 4))), "operating_margin": Decimal(str(round(12 + index * 1.9, 4))), "roe": Decimal(str(round(8 + index * 2.1, 4))), "debt_to_equity": Decimal(str(round(.25 + index * .11, 4))), "beta": Decimal(str(round(.75 + (index % 7) * .16, 4))), "rsi14": Decimal(str(round(40 + (index * 7) % 40, 4))), "sma20": Decimal(str(round(price * .98, 6))), "sma50": Decimal(str(round(price * (0.93 + index % 4 * .02), 6))), "sma200": Decimal(str(round(price * (0.88 + index % 5 * .025), 6))), "fair_value": Decimal(str(round(fair, 6))), "target_mean": Decimal(str(round(price * (1.05 + index % 6 * .06), 6))), "target_high": Decimal(str(round(price * (1.20 + index % 5 * .06), 6))), "target_low": Decimal(str(round(price * (.92 + index % 4 * .02), 6))), "analyst_buy": 8 - index % 3, "analyst_hold": 4 + index % 2, "analyst_sell": index % 3, "short_float": Decimal(str(round(1.2 + index * .41, 4))), "volatility_30d": Decimal(str(round(16 + index * 1.35, 4))), "quant_score": Decimal(str(round(54 + (index * 8) % 42, 2))), "quant_coverage": Decimal("100"), "metrics": {"one_year_return": round(-8 + index * 4.5, 2), "max_drawdown": round(-12 - index * .7, 2), "volume": round(500000 + index * 90000, 2), "sma20_vs_close": round(price / (price * .98) * 100 - 100, 2), "sma50_vs_close": round(price / (price * (.93 + index % 4 * .02)) * 100 - 100, 2), "sma200_vs_close": round(price / (price * (.88 + index % 5 * .025)) * 100 - 100, 2)},
    }
    if symbol == "MCD":
        result.update({
            "market_cap": Decimal("211600000000"),
            "pe": Decimal("19.97"),
            "dividend_yield": Decimal("2.77"),
            "revenue_growth": Decimal("3.30"),
            "eps_growth": Decimal("7.10"),
            "gross_margin": Decimal("57.20"),
            "operating_margin": Decimal("45.10"),
            "beta": Decimal("0.72"),
            "fair_value": Decimal(str(round(price * 1.12, 6))),
            "target_mean": Decimal(str(round(price * 1.08, 6))),
            "target_high": Decimal(str(round(price * 1.22, 6))),
            "target_low": Decimal(str(round(price * .91, 6))),
            "analyst_buy": 18,
            "analyst_hold": 12,
            "analyst_sell": 1,
            "quant_score": Decimal("74.50"),
            "metrics": {
                **result["metrics"],
                "headquarters": "Chicago, Illinois · USA",
                "business_moat": "Marca global, franquicias y red inmobiliaria",
                "profile_facts": [
                    {"label": "Modelo", "value": "Franquicias + inmuebles"},
                    {"label": "Países y territorios", "value": "100+"},
                    {"label": "Restaurantes del sistema", "value": "40.000+"},
                ],
                "dividend_yield_history": [
                    {"year": 2021, "value": 2.18}, {"year": 2022, "value": 2.30},
                    {"year": 2023, "value": 2.24}, {"year": 2024, "value": 2.42},
                    {"year": 2025, "value": 2.77},
                ],
                "pe_history": [
                    {"year": 2021, "value": 25.7}, {"year": 2022, "value": 28.1},
                    {"year": 2023, "value": 24.9}, {"year": 2024, "value": 22.8},
                    {"year": 2025, "value": 19.97},
                ],
                "eps_estimates": [
                    {"year": 2026, "value": 12.93}, {"year": 2027, "value": 13.98},
                    {"year": 2028, "value": 15.03},
                ],
                "data_quality_note": "Dataset demostrativo basado en la referencia visual; no representa una cotización o recomendación vigente.",
            },
        })
    return result


def _financial_data(index: int, statement_type: str, symbol: str = "", statement_year: int | None = None) -> dict:
    if symbol == "MCD" and statement_year in MCD_REVENUE:
        revenue = MCD_REVENUE[statement_year]
        eps = MCD_EPS[statement_year]
    else:
        revenue = 18_000_000_000 + index * 2_750_000_000
        eps = round(2.1 + index * .37, 2)
    if statement_type == "INCOME":
        return {"revenue": revenue, "ebitda": int(revenue * .44), "net_income": int(revenue * .31), "eps": eps, "gross_margin": .572 if symbol == "MCD" else round(.34 + index * .012, 3), "operating_margin": .451 if symbol == "MCD" else round(.15 + index * .009, 3)}
    if statement_type == "BALANCE":
        return {"assets": revenue * 2, "cash": int(revenue * .19), "debt": int(revenue * .44), "equity": int(revenue * 1.1), "shares": 1_000_000_000 - index * 7_500_000}
    return {"operating_cash_flow": int(revenue * .2), "free_cash_flow": int(revenue * .13), "capex": int(revenue * .07)}


def _seed_events(today: date) -> None:
    events = [("FOMC", "Decisión de tasas FOMC", 1, "HIGH", "US"), ("MACRO", "IPC Estados Unidos", 3, "HIGH", "US"), ("EARNINGS", "Temporada de resultados: mega-cap tech", 5, "MEDIUM", "US"), ("MACRO", "PIB trimestral", 8, "MEDIUM", "US"), ("PCE", "Inflación PCE subyacente", 12, "HIGH", "US")]
    for event_type, title, delta, impact, country in events:
        MarketEvent.objects.update_or_create(title=title, starts_at__date=today + timedelta(days=delta), defaults={"event_type": event_type, "starts_at": timezone.make_aware(datetime.combine(today + timedelta(days=delta), datetime.min.time().replace(hour=8))), "impact": impact, "country": country, "forecast": "—", "previous": "—", "notes": "Evento demo; reemplazar por proveedor conectado."})


def _seed_strategies(instruments, today):
    names = dict(Strategy.PRESET_CHOICES)
    descriptions = {"QUANT_LEADERS": "Los activos con mayor QuantPro y cobertura.", "UNDERVALUED": "Valor justo y múltiplos atractivos.", "QUALITY": "Rentabilidad y márgenes superiores.", "GARP": "Crecimiento a precio razonable.", "MOMENTUM": "Tendencia y fuerza relativa.", "DIVIDENDS": "Renta por dividendos y calidad.", "AI_SEMIS": "Temática IA, chips e infraestructura.", "ENERGY": "Energía tradicional y transición.", "DIVERGENCE": "Divergencia entre QuantPro y consenso de analistas."}
    for index, (key, label) in enumerate(names.items()):
        strategy, _ = Strategy.objects.update_or_create(key=key, defaults={"name": label, "description": descriptions[key], "active": True, "activated_at": timezone.now() - timedelta(days=30 + index * 7), "benchmark_symbol": "SPY"})
        selected = [item for i, item in enumerate(instruments) if (i + index) % 3 != 1][:10]
        for position, instrument in enumerate(selected):
            StrategyConstituent.objects.update_or_create(strategy=strategy, instrument=instrument, as_of=today, defaults={"weight": Decimal("0.1"), "signal_score": Decimal(str(55 + ((position + index) * 7) % 43))})
        cumulative = 1.0
        for delta in range(30, 0, -1):
            session = today - timedelta(days=delta)
            if session.weekday() >= 5:
                continue
            daily = ((index % 5) - 1) * .0003 + math.sin(delta / 8 + index) * .001
            cumulative *= 1 + daily
            StrategyReturn.objects.update_or_create(strategy=strategy, session_date=session, defaults={"portfolio_return": Decimal(str(round(daily, 8))), "cumulative_return": Decimal(str(round(cumulative - 1, 8))), "benchmark_return": Decimal(str(round((cumulative - 1) * .75, 8))), "drawdown": Decimal(str(round(min(0, cumulative - 1), 8)))})
