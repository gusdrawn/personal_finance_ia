from django.urls import path

from . import views

app_name = "altcoins"

urlpatterns = [
    path("", views.scanner, name="scanner"),
    path("instituciones/", views.institutions, name="institutions"),
    path("instituciones/<slug:slug>/", views.institution_detail, name="institution_detail"),
    path("operaciones/evidencia/registrar/", views.register_institutional_evidence, name="register_institutional_evidence"),
    path("descubrimiento/buscar/", views.discover, name="discover"),
    path("descubrimiento/<int:pk>/investigar/", views.research_discovery_candidate, name="research_discovery_candidate"),
    path("descubrimiento/<int:pk>/promover/", views.promote_discovery_candidate, name="promote_discovery_candidate"),
    path("operaciones/<str:action>/", views.run_operation, name="run_operation"),
    path("revision/", views.review_queue, name="review_queue"),
    path("revision/relacion/<int:pk>/", views.review_relationship, name="review_relationship"),
    path("<slug:slug>/", views.project_detail, name="project_detail"),
    path("<slug:slug>/watchlist/", views.toggle_watchlist, name="toggle_watchlist"),
    path("<slug:slug>/criterio/override/", views.set_criterion_override, name="set_criterion_override"),
    path("<slug:slug>/analisis-ia/", views.generate_ai_analysis, name="generate_ai_analysis"),
    path("<slug:slug>/evaluar-fundamentos/", views.evaluate_project_fundamentals, name="evaluate_project_fundamentals"),
]
