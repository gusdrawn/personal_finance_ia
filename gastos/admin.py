from django.contrib import admin
from .models import CategoriaIngreso, RegistroMensual, GastoAnual, GastoTrimestral


@admin.register(CategoriaIngreso)
class CategoriaIngresoAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'tipo', 'user', 'activo', 'orden']
    list_filter = ['tipo', 'activo', 'created_at']
    search_fields = ['nombre', 'user__username']
    list_editable = ['activo', 'orden']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Información', {'fields': ('user', 'nombre', 'tipo', 'orden')}),
        ('Configuración', {'fields': ('banco_defecto', 'activo')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )


@admin.register(RegistroMensual)
class RegistroMensualAdmin(admin.ModelAdmin):
    list_display = ['categoria', 'year', 'mes', 'monto', 'moneda', 'tipo']
    list_filter = ['year', 'mes', 'tipo', 'moneda', 'categoria__tipo']
    search_fields = ['categoria__nombre', 'user__username']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Período', {'fields': ('user', 'year', 'mes')}),
        ('Registro', {'fields': ('categoria', 'monto', 'moneda', 'tipo')}),
        ('Notas', {'fields': ('notas',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )
    date_hierarchy = 'created_at'


@admin.register(GastoAnual)
class GastoAnualAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'monto', 'mes_cobro', 'ahorro_mensual', 'user', 'activo']
    list_filter = ['mes_cobro', 'activo', 'user']
    search_fields = ['nombre', 'user__username']
    list_editable = ['activo']
    readonly_fields = ['created_at', 'updated_at', 'ahorro_mensual']


@admin.register(GastoTrimestral)
class GastoTrimestralAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'monto', 'trimestre', 'ahorro_mensual', 'user', 'activo']
    list_filter = ['trimestre', 'activo', 'user']
    search_fields = ['nombre', 'user__username']
    list_editable = ['activo']
    readonly_fields = ['created_at', 'updated_at', 'ahorro_mensual']
