from django.urls import path
from . import views
from configuracion.views import update_rates

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('gastos/', views.gastos_table, name='gastos-table'),
    path('patrimonio/', views.patrimonio, name='patrimonio'),
    path('departamentos/', views.departamentos, name='departamentos'),
    path('inversiones/', views.inversiones, name='inversiones'),
    path('configuracion/', views.configuracion, name='configuracion'),
    path('patrimonio/crear-activo/', views.crear_activo, name='crear_activo'),
    path('patrimonio/crear-pasivo/', views.crear_pasivo, name='crear_pasivo'),
    path('configuracion/actualizar-tasas/', update_rates, name='actualizar_tasas'),
    path('departamentos/estacionamientos/', views.estacionamientos, name='estacionamientos'),
    path('patrimonio/editar-activo/<int:pk>/', views.editar_activo, name='editar_activo'),
    path('patrimonio/borrar-activo/<int:pk>/', views.borrar_activo, name='borrar_activo'),
    path('patrimonio/editar-pasivo/<int:pk>/', views.editar_pasivo, name='editar_pasivo'),
    path('patrimonio/borrar-pasivo/<int:pk>/', views.borrar_pasivo, name='borrar_pasivo'),
    path('departamentos/editar/<int:pk>/', views.editar_departamento, name='editar_departamento'),
    path('departamentos/borrar/<int:pk>/', views.borrar_departamento, name='borrar_departamento'),
    path('inversiones/editar/<int:pk>/', views.editar_inversion, name='editar_inversion'),
    path('inversiones/borrar/<int:pk>/', views.borrar_inversion, name='borrar_inversion'),
]
