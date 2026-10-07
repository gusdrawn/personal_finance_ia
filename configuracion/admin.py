from django.contrib import admin
from .models import Banco, CredencialIntegracion, Producto, TipoCambio, ConfiguracionGeneral
from .services.bancos import banco_en_uso


@admin.register(Banco)
class BancoAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'orden', 'activo']
    list_filter = ['activo', 'created_at']
    search_fields = ['nombre']
    list_editable = ['orden', 'activo']
    readonly_fields = ['created_at', 'updated_at']

    def has_delete_permission(self, request, obj=None):
        return super().has_delete_permission(request, obj) and (
            obj is None or not banco_en_uso(obj)
        )


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'user', 'banco', 'tipo', 'ultimos4', 'origen', 'requiere_revision', 'orden', 'activo']
    list_filter = ['banco', 'tipo', 'origen', 'requiere_revision', 'activo']
    search_fields = ['nombre', 'banco__nombre', 'user__username', 'ultimos4']
    list_editable = ['orden', 'activo']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Información', {'fields': ('user', 'banco', 'nombre', 'tipo', 'ultimos4', 'origen', 'orden')}),
        ('Estado', {'fields': ('activo', 'requiere_revision', 'reemplazado_por')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )


@admin.register(TipoCambio)
class TipoCambioAdmin(admin.ModelAdmin):
    list_display = ['fecha', 'uf', 'dolar', 'fuente']
    list_filter = ['fuente', 'fecha']
    search_fields = ['fuente']
    date_hierarchy = 'fecha'
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Tipo de Cambio', {'fields': ('fecha', 'uf', 'dolar', 'fuente')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )


@admin.register(ConfiguracionGeneral)
class ConfiguracionGeneralAdmin(admin.ModelAdmin):
    list_display = ['user', 'banco_defecto_salario', 'dia_recordatorio_alimentacion']
    list_filter = ['actualizar_tc_automatico']
    search_fields = ['user__username']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Usuario', {'fields': ('user',)}),
        ('Configuración', {
            'fields': ('banco_defecto_salario', 'dia_recordatorio_alimentacion', 'mostrar_modo_lectura', 'actualizar_tc_automatico')
        }),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )


@admin.register(CredencialIntegracion)
class CredencialIntegracionAdmin(admin.ModelAdmin):
    list_display = ["provider", "key_prefix", "active", "updated_by", "updated_at"]
    list_filter = ["provider", "active"]
    readonly_fields = ["key_prefix", "updated_by", "updated_at"]
