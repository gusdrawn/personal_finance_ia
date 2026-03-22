from django.contrib import admin
from .models import Activo, Pasivo, SnapshotPatrimonio, MiniSesion, LineaMiniSesion


@admin.register(Activo)
class ActivoAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'tipo', 'tipo_liquidez', 'monto_clp', 'monto_usd', 'user']
    list_filter = ['tipo', 'tipo_liquidez', 'user', 'created_at']
    search_fields = ['nombre', 'user__username']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Información', {'fields': ('user', 'nombre', 'tipo', 'tipo_liquidez')}),
        ('Montos', {'fields': ('monto_clp', 'monto_usd')}),
        ('Relacionado', {'fields': ('departamento',)}),
        ('Notas', {'fields': ('notas',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )


@admin.register(Pasivo)
class PasivoAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'tipo', 'monto_clp', 'monto_usd', 'user']
    list_filter = ['tipo', 'user', 'created_at']
    search_fields = ['nombre', 'user__username']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(SnapshotPatrimonio)
class SnapshotPatrimonioAdmin(admin.ModelAdmin):
    list_display = ['user', 'fecha', 'patrimonio_neto_clp', 'activos_liquidos_clp']
    list_filter = ['user', 'fecha']
    search_fields = ['user__username']
    readonly_fields = ['created_at', 'updated_at']
    date_hierarchy = 'fecha'


class LineaMiniSesionInline(admin.TabularInline):
    model = LineaMiniSesion
    extra = 1
    fields = ['concepto', 'monto', 'orden']


@admin.register(MiniSesion)
class MiniSesionAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'fecha', 'tipo', 'user', 'compartible']
    list_filter = ['tipo', 'compartible', 'user', 'fecha']
    search_fields = ['nombre', 'user__username']
    readonly_fields = ['created_at', 'updated_at']
    inlines = [LineaMiniSesionInline]
    fieldsets = (
        ('Información', {'fields': ('user', 'nombre', 'fecha', 'tipo')}),
        ('Configuración', {'fields': ('compartible', 'activo_relacionado')}),
        ('Notas', {'fields': ('notas',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )
