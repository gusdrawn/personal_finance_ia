from django.urls import include, path
from rest_framework.routers import DefaultRouter

from gastos import views


router = DefaultRouter()
router.register(r"categorias", views.CategoriaTematicaViewSet, basename="categoria-gasto")
router.register(r"manuales", views.MovimientoManualViewSet, basename="movimiento-manual")
router.register(r"gastos-programados", views.GastoProgramadoViewSet, basename="gasto-programado")

urlpatterns = [
    path("", include(router.urls)),
    path("resumen/", views.gastos_resumen, name="gastos_resumen_api"),
    path("detalle/", views.gastos_detalle, name="gastos_detalle_api"),
    path("facturados/<int:pk>/pago-real/", views.registrar_pago_real, name="gastos_pago_real_api"),
    path("exportar.xlsx", views.exportar_gastos, name="gastos_exportar_api"),
]
