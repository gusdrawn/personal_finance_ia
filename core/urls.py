from django.urls import path
from . import views
from configuracion.views import update_rates, take_snapshot

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('gastos/', views.gastos_table, name='gastos-table'),
    path('patrimonio/', views.patrimonio, name='patrimonio'),
    path('departamentos/', views.departamentos, name='departamentos'),
    path('activos/', views.activos_view, name='activos'),
    path('cuentas-por-cobrar/', views.cuentas_por_cobrar_view, name='cuentas_por_cobrar'),
    path('pasivos/', views.pasivos_view, name='pasivos'),
    path('configuracion/', views.configuracion, name='configuracion'),
    path('calendario/', views.calendario, name='calendario'),
    
    # Activos CRUD
    path('activos/crear/', views.crear_activo, name='crear_activo'),
    path('activos/editar/<int:pk>/', views.editar_activo, name='editar_activo'),
    path('activos/borrar/<int:pk>/', views.borrar_activo, name='borrar_activo'),

    # Cuentas por cobrar
    path('cuentas-por-cobrar/crear/', views.crear_cuenta_por_cobrar, name='crear_cuenta_por_cobrar'),
    path('cuentas-por-cobrar/detalle/agregar/', views.agregar_detalle_cuenta_por_cobrar, name='agregar_detalle_cuenta_por_cobrar'),
    path('cuentas-por-cobrar/detalle/<int:pk>/editar/', views.editar_detalle_cuenta_por_cobrar, name='editar_detalle_cuenta_por_cobrar'),
    path('cuentas-por-cobrar/abono/agregar/', views.registrar_abono_cuenta_por_cobrar, name='registrar_abono_cuenta_por_cobrar'),
    path('cuentas-por-cobrar/abono/<int:pk>/borrar/', views.borrar_abono_cuenta_por_cobrar, name='borrar_abono_cuenta_por_cobrar'),
    path('cuentas-por-cobrar/<int:pk>/renovar/', views.renovar_cuenta_por_cobrar, name='renovar_cuenta_por_cobrar'),
    path('cuentas-por-cobrar/<int:pk>/alternar/', views.alternar_cuenta_por_cobrar, name='alternar_cuenta_por_cobrar'),
    
    # Pasivos CRUD
    path('pasivos/crear/', views.crear_pasivo, name='crear_pasivo'),
    path('pasivos/editar/<int:pk>/', views.editar_pasivo, name='editar_pasivo'),
    path('pasivos/borrar/<int:pk>/', views.borrar_pasivo, name='borrar_pasivo'),
    
    # Departamentos CRUD
    path('departamentos/crear/', views.crear_departamento, name='crear_departamento'),
    path('departamentos/editar/<int:pk>/', views.editar_departamento, name='editar_departamento'),
    path('departamentos/borrar/<int:pk>/', views.borrar_departamento, name='borrar_departamento'),
    path('departamentos/estacionamientos/', views.estacionamientos, name='estacionamientos'),
    path('departamentos/<int:depto_pk>/arrendatario/crear/', views.crear_arrendatario, name='crear_arrendatario'),
    path('departamentos/arrendatario/editar/<int:pk>/', views.editar_arrendatario, name='editar_arrendatario'),
    path('departamentos/arrendatario/borrar/<int:pk>/', views.borrar_arrendatario, name='borrar_arrendatario'),
    path('departamentos/<int:depto_pk>/credito/crear/', views.crear_credito, name='crear_credito'),
    path('departamentos/credito/editar/<int:pk>/', views.editar_credito, name='editar_credito'),
    path('departamentos/credito/borrar/<int:pk>/', views.borrar_credito, name='borrar_credito'),
    
    # Gastos
    path('gastos/bulk/', views.bulk_gastos, name='bulk_gastos'),
    path('gastos/categoria-stats/<int:cat_pk>/', views.get_category_data, name='categoria_stats'),
    
    # Configuracion
    path('configuracion/actualizar-tasas/', update_rates, name='actualizar_tasas'),
    path('configuracion/tomar-snapshot/', take_snapshot, name='tomar_snapshot'),
    path('configuracion/crear-categoria/', views.crear_categoria, name='crear_categoria'),
    path('configuracion/editar-categoria/<int:pk>/', views.editar_categoria, name='editar_categoria'),
    path('configuracion/borrar-categoria/<int:pk>/', views.borrar_categoria, name='borrar_categoria'),
    path('configuracion/crear-banco/', views.crear_banco, name='crear_banco'),
    path('configuracion/editar-banco/<int:pk>/', views.editar_banco, name='editar_banco'),
    path('configuracion/borrar-banco/<int:pk>/', views.borrar_banco, name='borrar_banco'),
    path('configuracion/crear-producto/', views.crear_producto, name='crear_producto'),
    path('configuracion/editar-producto/<int:pk>/', views.editar_producto, name='editar_producto'),
    path('configuracion/borrar-producto/<int:pk>/', views.borrar_producto, name='borrar_producto'),
    path('configuracion/vincular-producto/', views.vincular_producto_reemplazo, name='vincular_producto_reemplazo'),
    path('configuracion/apple-pay/key/crear/', views.crear_apple_pay_key, name='crear_apple_pay_key'),
    path('configuracion/apple-pay/key/<int:pk>/revocar/', views.revocar_apple_pay_key, name='revocar_apple_pay_key'),
    path('configuracion/notificaciones/guardar/', views.guardar_notificaciones, name='guardar_notificaciones'),
    path('configuracion/notificaciones/probar/', views.probar_notificaciones_telegram, name='probar_notificaciones_telegram'),
    path('configuracion/notificaciones/retirar/', views.retirar_notificaciones_telegram, name='retirar_notificaciones_telegram'),
    path('configuracion/notificaciones/limites/guardar/', views.guardar_limite_categoria, name='guardar_limite_categoria'),
    path('configuracion/notificaciones/limites/<int:pk>/borrar/', views.borrar_limite_categoria, name='borrar_limite_categoria'),
    path('configuracion/integraciones/<str:provider>/guardar/', views.guardar_market_api_key, name='guardar_market_api_key'),
    path('configuracion/integraciones/<str:provider>/retirar/', views.retirar_market_api_key, name='retirar_market_api_key'),
    
    # Calendario
    path('calendario/programado/crear/', views.crear_gasto_programado, name='crear_gasto_programado'),
    path('calendario/programado/editar/<int:pk>/', views.editar_gasto_programado, name='editar_gasto_programado'),
    path('calendario/programado/borrar/<int:pk>/', views.borrar_gasto_programado, name='borrar_gasto_programado'),
    
    # Legacy redirect
    path('inversiones/', views.activos_view, name='inversiones'),
]
