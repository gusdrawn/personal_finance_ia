from django.contrib import admin

from .models import (
    AnalystSnapshot, DailyBar, FinancialStatementSnapshot, Instrument, InstrumentMetricSnapshot,
    InstrumentUserState, MarketEvent, Strategy, StrategyConstituent, StrategyReturn, Theme,
)

for model in (Instrument, Theme, DailyBar, InstrumentMetricSnapshot, FinancialStatementSnapshot,
              AnalystSnapshot, MarketEvent, InstrumentUserState, Strategy, StrategyConstituent,
              StrategyReturn):
    admin.site.register(model)
