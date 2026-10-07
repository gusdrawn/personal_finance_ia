from django.urls import path

from .views import PendienteDetailAPIView, PendientesAPIView

urlpatterns = [
    path("", PendientesAPIView.as_view(), name="api_pendientes"),
    path("<int:pk>/", PendienteDetailAPIView.as_view(), name="api_pendiente_detail"),
]
