from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CategoriaIngresoViewSet, RegistroMensualViewSet,
    GastoAnualViewSet, GastoTrimestralViewSet
)

router = DefaultRouter()
router.register(r'categorias', CategoriaIngresoViewSet, basename='categoria')
router.register(r'registros', RegistroMensualViewSet, basename='registro')
router.register(r'gastos-anuales', GastoAnualViewSet, basename='gasto-anual')
router.register(r'gastos-trimestrales', GastoTrimestralViewSet, basename='gasto-trimestral')

urlpatterns = [
    path('', include(router.urls)),
]
