from datetime import date

from .base import AssetCandidate, BarObservation, MarketDataProvider, ProviderCapabilities


class DemoProvider:
    name = "DEMO"

    def capabilities(self):
        return ProviderCapabilities(catalog=True, daily_bars=True, fundamentals=True, analysts=True, events=True, news=False)

    def search_instruments(self, query):
        from trading.models import Instrument
        return [AssetCandidate(symbol=item.symbol, name=item.name, asset_type=item.asset_type, exchange=item.exchange, currency=item.currency, sector=item.sector, industry=item.industry) for item in Instrument.objects.filter(active=True).filter(symbol__icontains=query)[:20]]

    def fetch_daily_bars(self, symbol, start=None, end=None):
        return []

    def fetch_metrics(self, symbol):
        return {}

    def fetch_financial_statements(self, symbol):
        return []

    def fetch_analyst_data(self, symbol):
        return {}

    def fetch_events(self, start, end):
        return []

    def fetch_news(self, symbol):
        return []
