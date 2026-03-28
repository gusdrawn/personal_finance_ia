from django.contrib import admin
from .models import Activo, HistorialActivo, Pasivo, SnapshotPatrimonio, MiniSesion, LineaMiniSesion


@admin.register(Activo)
class ActivoAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'tipo', 'horizonte_temporal', 'es_liquido', 'monto_clp', 'monto_usd', 'activo', 'user']
    list_filter = ['tipo', 'horizonte_temporal', 'es_liquido', 'activo', 'user', 'created_at']
    search_fields = ['nombre', 'user__username']
    list_editable = ['activo', 'es_liquido']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Información', {'fields': ('user', 'nombre', 'tipo', 'horizonte_temporal', 'es_liquido', 'activo')}),
        ('Montos', {'fields': ('monto_clp', 'monto_usd')}),
        ('Relacionado', {'fields': ('departamento',)}),
        ('Notas', {'fields': ('notas',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )


@admin.register(HistorialActivo)
class HistorialActivoAdmin(admin.ModelAdmin):
    list_display = ['activo', 'fecha', 'monto_clp', 'monto_usd']
    list_filter = ['activo', 'fecha']
    search_fields = ['activo__nombre', 'activo__user__username']
    readonly_fields = ['created_at', 'updated_at']
    date_hierarchy = 'fecha'


@admin.register(Pasivo)
class PasivoAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'tipo', 'monto_clp', 'monto_usd', 'activo', 'user']
    list_filter = ['tipo', 'activo', 'user', 'created_at']
    search_fields = ['nombre', 'user__username']
    list_editable = ['activo']
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
