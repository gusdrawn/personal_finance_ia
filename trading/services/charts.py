from __future__ import annotations

from datetime import date, timedelta
from math import sqrt

from django.core.serializers.json import DjangoJSONEncoder


def chart_payload(instrument, chart_key="price", range_key="1Y"):
    days = {"1M": 31, "3M": 93, "6M": 186, "1Y": 366, "5Y": 1825}.get(range_key, 366)
    bars = list(instrument.daily_bars.exclude(source="DEMO").filter(session_date__gte=date.today() - timedelta(days=days)).order_by("session_date"))
    closes = [float(bar.close) for bar in bars]
    data = []
    if chart_key in {"price", "technical"}:
        data = [{"x": bar.session_date.isoformat(), "o": float(bar.open), "h": float(bar.high), "l": float(bar.low), "c": float(bar.close), "v": float(bar.volume or 0)} for bar in bars]
        series = [{"id": "price", "label": "OHLC", "type": "candlestick", "unit": instrument.currency, "data": data}, {"id": "volume", "label": "Volumen", "type": "histogram", "unit": "shares", "data": [{"x": bar.session_date.isoformat(), "y": float(bar.volume or 0)} for bar in bars]}]
        for period, label in ((20, "SMA 20"), (50, "SMA 50"), (200, "SMA 200")):
            values = []
            for index, bar in enumerate(bars):
                window = closes[max(0, index - period + 1):index + 1]
                values.append({"x": bar.session_date.isoformat(), "y": round(sum(window) / len(window), 6)})
            series.append({"id": f"sma{period}", "label": label, "type": "line", "unit": instrument.currency, "data": values})
    elif chart_key == "performance":
        first = closes[0] if closes else 1
        series = [{"id": "normalized", "label": "Rendimiento relativo", "type": "line", "unit": "%", "data": [{"x": bar.session_date.isoformat(), "y": round((float(bar.close) / first - 1) * 100, 2)} for bar in bars]}]
    elif chart_key == "drawdown":
        peak = 0
        series = [{"id": "drawdown", "label": "Drawdown", "type": "area", "unit": "%", "data": []}]
        for bar in bars:
            peak = max(peak, float(bar.close))
            series[0]["data"].append({"x": bar.session_date.isoformat(), "y": round((float(bar.close) / peak - 1) * 100, 2)})
    elif chart_key == "financials":
        statements = {statement.statement_type: statement for statement in instrument.financial_statements.exclude(source="DEMO")}
        series = []
        for key, label in (("revenue", "Ingresos"), ("ebitda", "EBITDA"), ("net_income", "Utilidad neta"), ("free_cash_flow", "FCF")):
            rows = []
            for statement in statements.values():
                if key in statement.data:
                    rows.append({"x": statement.period_end.isoformat(), "y": statement.data[key]})
            if rows:
                series.append({"id": key, "label": label, "type": "bar", "unit": "USD", "data": rows})
    else:
        series = []
    sources = sorted({bar.source for bar in bars})
    warnings = [] if series else ["No hay datos actuales disponibles para esta gráfica. Usa “Actualizar información ahora”."]
    return {"chart": chart_key, "instrument": {"id": instrument.id, "symbol": instrument.symbol, "name": instrument.name}, "range": range_key, "as_of": bars[-1].session_date.isoformat() if bars else None, "currency": instrument.currency, "timezone": "America/New_York", "series": series, "markers": [], "warnings": warnings, "source": ", ".join(sources) or None}
