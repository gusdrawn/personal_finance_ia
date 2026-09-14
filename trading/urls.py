from django.urls import path

from . import views

app_name = "trading"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("operations/<str:action>/", views.run_operation, name="run_operation"),
    path("research/", views.research, name="research"),
    path("strategies/", views.strategies, name="strategies"),
    path("calendar/", views.calendar, name="calendar"),
    path("watchlist/", lambda request: views.user_list(request, discarded=False), name="watchlist"),
    path("discarded/", lambda request: views.user_list(request, discarded=True), name="discarded"),
    path("instrument/<int:pk>/", views.instrument_detail, name="instrument_detail"),
    path("api/search/", views.api_search, name="api_search"),
    path("api/import/", views.api_import_symbol, name="api_import_symbol"),
    path("api/screener/", views.api_screener, name="api_screener"),
    path("api/instruments/<int:pk>/chart/", views.api_chart, name="api_chart"),
    path("api/instruments/<int:pk>/chart/<str:chart_key>/", views.api_chart, name="api_chart_named"),
    path("api/instruments/<int:pk>/watch/", views.api_watch, name="api_watch"),
    path("api/instruments/<int:pk>/broker-status/", views.api_broker_status, name="api_broker_status"),
    path("api/instruments/<int:pk>/state/", views.api_state, name="api_state"),
    path("api/ai/research/", views.api_ai_research, name="api_ai_research"),
]
