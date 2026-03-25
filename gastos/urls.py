from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CategoriaIngresoViewSet, RegistroMensualViewSet,
    GastoProgramadoViewSet
)

router = DefaultRouter()
router.register(r'categorias', CategoriaIngresoViewSet, basename='categoria')
router.register(r'registros', RegistroMensualViewSet, basename='registro')
router.register(r'gastos-programados', GastoProgramadoViewSet, basename='gasto-programado')


urlpatterns = [
    path('', include(router.urls)),
]
