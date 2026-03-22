from django.contrib import admin
from .models import Banco, Producto, TipoCambio, ConfiguracionGeneral


@admin.register(Banco)
class BancoAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'orden', 'activo']
    list_filter = ['activo', 'created_at']
    search_fields = ['nombre']
    list_editable = ['orden', 'activo']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'banco', 'tipo', 'orden', 'activo']
    list_filter = ['banco', 'tipo', 'activo']
    search_fields = ['nombre', 'banco__nombre']
    list_editable = ['orden', 'activo']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Información', {'fields': ('banco', 'nombre', 'tipo', 'orden')}),
        ('Estado', {'fields': ('activo',)}),
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
