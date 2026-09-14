from __future__ import annotations

from django.conf import settings
from django.db import models


class Instrument(models.Model):
    ASSET_TYPES = (
        ("STOCK", "Acción"), ("ETF", "ETF"), ("CRYPTO", "Cripto"),
        ("FOREX", "Forex"), ("COMMODITY", "Commoditie"), ("INDEX", "Índice"),
        ("FUTURE", "Futuro"),
    )
    symbol = models.CharField(max_length=32)
    name = models.CharField(max_length=220)
    asset_type = models.CharField(max_length=16, choices=ASSET_TYPES, default="STOCK")
    exchange = models.CharField(max_length=80, blank=True)
    currency = models.CharField(max_length=8, default="USD")
    country = models.CharField(max_length=80, blank=True)
    sector = models.CharField(max_length=120, blank=True)
    industry = models.CharField(max_length=160, blank=True)
    isin = models.CharField(max_length=32, blank=True)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    source = models.CharField(max_length=32, default="DEMO")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("symbol",)
        constraints = [models.UniqueConstraint(fields=("symbol", "exchange"), name="unique_trading_instrument")]

    def __str__(self):
        return f"{self.symbol} — {self.name}"

    @property
    def latest_bar(self):
        return self.daily_bars.order_by("-session_date").first()

    @property
    def latest_metrics(self):
        return self.metric_snapshots.order_by("-observed_at").first()


class InstrumentExternalId(models.Model):
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="external_ids")
    provider = models.CharField(max_length=40)
    external_id = models.CharField(max_length=180)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("provider", "external_id"), name="unique_trading_external_id")]


class Theme(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class InstrumentTheme(models.Model):
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="theme_links")
    theme = models.ForeignKey(Theme, on_delete=models.CASCADE, related_name="instrument_links")
    confidence = models.DecimalField(max_digits=5, decimal_places=2, default=100)
    source = models.CharField(max_length=32, default="MANUAL")

    class Meta:
        constraints = [models.UniqueConstraint(fields=("instrument", "theme"), name="unique_trading_theme_link")]


class DailyBar(models.Model):
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="daily_bars")
    session_date = models.DateField()
    open = models.DecimalField(max_digits=24, decimal_places=8)
    high = models.DecimalField(max_digits=24, decimal_places=8)
    low = models.DecimalField(max_digits=24, decimal_places=8)
    close = models.DecimalField(max_digits=24, decimal_places=8)
    adjusted_close = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    volume = models.DecimalField(max_digits=30, decimal_places=4, null=True, blank=True)
    source = models.CharField(max_length=32, default="DEMO")

    class Meta:
        ordering = ("session_date",)
        constraints = [models.UniqueConstraint(fields=("instrument", "session_date"), name="unique_trading_daily_bar")]


class InstrumentMetricSnapshot(models.Model):
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="metric_snapshots")
    observed_at = models.DateTimeField()
    metrics = models.JSONField(default=dict, blank=True)
    market_cap = models.DecimalField(max_digits=30, decimal_places=2, null=True, blank=True)
    pe = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    ps = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    pb = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    ev_ebitda = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    dividend_yield = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    revenue_growth = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    eps_growth = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    gross_margin = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    operating_margin = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    roe = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    debt_to_equity = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    beta = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    rsi14 = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    sma20 = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    sma50 = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    sma200 = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    fair_value = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    target_mean = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    target_high = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    target_low = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    analyst_buy = models.PositiveIntegerField(null=True, blank=True)
    analyst_hold = models.PositiveIntegerField(null=True, blank=True)
    analyst_sell = models.PositiveIntegerField(null=True, blank=True)
    short_float = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    volatility_30d = models.DecimalField(max_digits=8, decimal_places=4, null=True, blank=True)
    quant_score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    quant_coverage = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    source = models.CharField(max_length=32, default="DEMO")

    class Meta:
        ordering = ("-observed_at",)


class FinancialStatementSnapshot(models.Model):
    STATEMENT_TYPES = (("INCOME", "Resultados"), ("BALANCE", "Balance"), ("CASHFLOW", "Flujo de caja"))
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="financial_statements")
    statement_type = models.CharField(max_length=16, choices=STATEMENT_TYPES)
    period_end = models.DateField()
    fiscal_period = models.CharField(max_length=16, blank=True)
    data = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=32, default="DEMO")

    class Meta:
        ordering = ("-period_end",)
        constraints = [models.UniqueConstraint(fields=("instrument", "statement_type", "period_end"), name="unique_trading_statement")]


class AnalystSnapshot(models.Model):
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="analyst_snapshots")
    observed_at = models.DateTimeField()
    provider = models.CharField(max_length=32, default="DEMO")
    consensus = models.CharField(max_length=24, blank=True)
    target_mean = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    target_high = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    target_low = models.DecimalField(max_digits=24, decimal_places=8, null=True, blank=True)
    ratings = models.JSONField(default=dict, blank=True)
    source_url = models.URLField(blank=True)


class MarketEvent(models.Model):
    IMPACT_CHOICES = (("HIGH", "Alto"), ("MEDIUM", "Medio"), ("LOW", "Bajo"))
    event_type = models.CharField(max_length=40)
    title = models.CharField(max_length=240)
    starts_at = models.DateTimeField()
    country = models.CharField(max_length=80, blank=True)
    impact = models.CharField(max_length=12, choices=IMPACT_CHOICES, default="MEDIUM")
    actual = models.CharField(max_length=80, blank=True)
    forecast = models.CharField(max_length=80, blank=True)
    previous = models.CharField(max_length=80, blank=True)
    source_url = models.URLField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ("starts_at",)


class InstrumentUserState(models.Model):
    BROKER_STATUS = (("UNKNOWN", "No comprobado"), ("AVAILABLE", "Disponible"), ("UNAVAILABLE", "No disponible"))
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="trading_instrument_states")
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE, related_name="user_states")
    watched = models.BooleanField(default=False)
    discarded = models.BooleanField(default=False)
    broker_status = models.CharField(max_length=16, choices=BROKER_STATUS, default="UNKNOWN")
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("user", "instrument"), name="unique_user_trading_state")]


class Strategy(models.Model):
    PRESET_CHOICES = (
        ("QUANT_LEADERS", "Quant leaders"), ("UNDERVALUED", "Subvaloradas"),
        ("QUALITY", "Quality"), ("GARP", "GARP"), ("MOMENTUM", "Momentum"),
        ("DIVIDENDS", "Dividendos"), ("AI_SEMIS", "IA y semiconductores"),
        ("ENERGY", "Energía"), ("DIVERGENCE", "Divergencia Quant–Wall Street"),
    )
    key = models.CharField(max_length=32, choices=PRESET_CHOICES, unique=True)
    name = models.CharField(max_length=140)
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    benchmark_symbol = models.CharField(max_length=24, default="SPY")


class StrategyConstituent(models.Model):
    strategy = models.ForeignKey(Strategy, on_delete=models.CASCADE, related_name="constituents")
    instrument = models.ForeignKey(Instrument, on_delete=models.CASCADE)
    as_of = models.DateField()
    weight = models.DecimalField(max_digits=8, decimal_places=5, default=0)
    signal_score = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("strategy", "instrument", "as_of"), name="unique_strategy_constituent")]


class StrategyReturn(models.Model):
    strategy = models.ForeignKey(Strategy, on_delete=models.CASCADE, related_name="returns")
    session_date = models.DateField()
    portfolio_return = models.DecimalField(max_digits=12, decimal_places=8, default=0)
    cumulative_return = models.DecimalField(max_digits=12, decimal_places=8, default=0)
    benchmark_return = models.DecimalField(max_digits=12, decimal_places=8, default=0)
    drawdown = models.DecimalField(max_digits=12, decimal_places=8, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("strategy", "session_date"), name="unique_strategy_return")]


class AIResearchRun(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="trading_ai_runs")
    prompt = models.TextField()
    normalized_filter = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    evidence = models.JSONField(default=list, blank=True)
    model_name = models.CharField(max_length=120, default="deterministic-copilot")
    created_at = models.DateTimeField(auto_now_add=True)


class TradingSyncRun(models.Model):
    STATUS_CHOICES = (("RUNNING", "Ejecutando"), ("SUCCESS", "Correcto"), ("PARTIAL", "Parcial"), ("FAILED", "Fallido"))
    provider = models.CharField(max_length=40)
    operation = models.CharField(max_length=80)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default="RUNNING")
    records_received = models.PositiveIntegerField(default=0)
    records_created = models.PositiveIntegerField(default=0)
    records_updated = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True)
