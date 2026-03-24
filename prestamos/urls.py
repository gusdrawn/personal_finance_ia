from django.urls import path
from . import views

urlpatterns = [
    path('', views.prestamos_index, name='prestamos_index'),
    path('crear/', views.crear_prestamo, name='crear_prestamo'),
    path('editar/<int:pk>/', views.editar_prestamo, name='editar_prestamo'),
    path('borrar/<int:pk>/', views.borrar_prestamo, name='borrar_prestamo'),
]
