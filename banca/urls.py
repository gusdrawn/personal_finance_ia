from django.urls import path

from . import views

urlpatterns = [
    path("", views.banca_dashboard, name="banca_dashboard"),
    path("api/apple-pay/ingest/", views.banca_apple_pay_ingest, name="banca_apple_pay_ingest"),
    path("api/apple-pay/keys/", views.banca_apple_pay_keys, name="banca_apple_pay_keys"),
    path("api/apple-pay/keys/<int:pk>/revoke/", views.banca_apple_pay_key_revoke, name="banca_apple_pay_key_revoke"),
    path("movimientos/", views.banca_movimientos, name="banca_movimientos"),
    path("importar/", views.banca_importar, name="banca_importar"),
    path("clasificacion/recalcular/", views.banca_recalcular_clasificaciones, name="banca_recalcular_clasificaciones"),
    path("importar/<int:pk>/reanalizar/", views.banca_reanalizar_importacion, name="banca_reanalizar_importacion"),
    path("importar/<int:pk>/pago-aseo/", views.banca_vincular_pago_aseo, name="banca_vincular_pago_aseo"),
    path("movimientos/<int:pk>/reclasificar/", views.banca_reclasificar, name="banca_reclasificar"),
    path("eventos-neat/<int:pk>/reclasificar/", views.banca_reclasificar_neat, name="banca_reclasificar_neat"),
    path("movimientos/<int:pk>/revisar/", views.banca_confirmar_revision, name="banca_confirmar_revision"),
    path("movimientos/<int:pk>/resolver/", views.banca_resolver_movimiento, name="banca_resolver_movimiento"),
    path("movimientos/<int:pk>/asignar-prestamo/", views.banca_asignar_prestamo, name="banca_asignar_prestamo"),
    path("movimientos/<int:pk>/asignar-cuenta-cobrar/", views.banca_asignar_cuenta_cobrar_movimiento, name="banca_asignar_cuenta_cobrar_movimiento"),
    path("eventos-neat/<int:pk>/asignar-cuenta-cobrar/", views.banca_asignar_cuenta_cobrar_neat, name="banca_asignar_cuenta_cobrar_neat"),
    path("movimientos/<int:pk>/asignar-abono-cuenta-cobrar/", views.banca_asignar_abono_cuenta_cobrar, name="banca_asignar_abono_cuenta_cobrar"),
    path("sync/", views.banca_sync, name="banca_sync"),
    path("fuentes-externas/", views.banca_fuentes_externas, name="banca_fuentes_externas"),
    path("fuentes-externas/importar/", views.banca_importar_fuente_externa, name="banca_importar_fuente_externa"),
    path("fuentes-externas/importaciones/<int:pk>/descargar/", views.banca_descargar_importacion_externa, name="banca_descargar_importacion_externa"),
    path("fuentes-externas/sync/", views.banca_sync_fuentes_externas, name="banca_sync_fuentes_externas"),
    path("fuentes-externas/<int:pk>/sync/", views.banca_sync_fuente_externa, name="banca_sync_fuente_externa"),
    path("fuentes-externas/<int:pk>/configurar/", views.banca_configurar_fuente_externa, name="banca_configurar_fuente_externa"),
    path("fuentes-externas/<int:pk>/probar/", views.banca_probar_fuente_externa, name="banca_probar_fuente_externa"),
    path("fuentes-externas/propiedad/vincular/", views.banca_vincular_propiedad_externa, name="banca_vincular_propiedad_externa"),

    # Deudas
    path("deudas/", views.banca_deudas, name="banca_deudas"),

    # Categorías temáticas
    path("categorias/", views.banca_categorias, name="banca_categorias"),
    path("categorias/<int:pk>/editar/", views.banca_categoria_editar, name="banca_categoria_editar"),
    path("categorias/<int:pk>/borrar/", views.banca_categoria_borrar, name="banca_categoria_borrar"),
    path("categorias/sembrar/", views.banca_sembrar_categorias, name="banca_sembrar_categorias"),

    # Configuración (credenciales, parámetros, contrapartes)
    path("config/", views.banca_config, name="banca_config"),
    path("config/credencial/", views.banca_guardar_credencial, name="banca_guardar_credencial"),
    path("config/credencial/<int:pk>/borrar/", views.banca_borrar_credencial, name="banca_borrar_credencial"),
    path("config/parametros/", views.banca_guardar_parametros, name="banca_guardar_parametros"),
    path("config/modelos-ia/refrescar/", views.banca_refrescar_modelos, name="banca_refrescar_modelos"),
    path("config/contraparte/", views.banca_guardar_contraparte, name="banca_guardar_contraparte"),
    path("config/contraparte/<int:pk>/borrar/", views.banca_borrar_contraparte, name="banca_borrar_contraparte"),

    # Historial de sincronización
    path("sync-logs/", views.banca_sync_logs, name="banca_sync_logs"),
]
