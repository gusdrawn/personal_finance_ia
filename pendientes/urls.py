from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard_pendientes, name="dashboard_pendientes"),
    path("tarea/crear/", views.crear_tarea_manual, name="crear_tarea_pendiente"),
    path("propiedad/configurar/", views.configurar_propiedad, name="pendientes_configurar_propiedad_manual"),
    path("propiedad/<int:departamento_id>/configurar/", views.configurar_propiedad, name="pendientes_configurar_propiedad"),
    path("<int:pk>/resolver/", views.resolver_pendiente, name="resolver_pendiente"),
]
