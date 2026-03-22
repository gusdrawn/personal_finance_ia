from django.contrib import admin
from .models import Inversion, HistorialInversion


@admin.register(Inversion)
class InversionAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'tipo', 'monto_clp', 'monto_usd', 'porcentaje_cartera', 'user', 'activo']
    list_filter = ['tipo', 'user', 'activo', 'created_at']
    search_fields = ['nombre', 'user__username']
    list_editable = ['activo']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(HistorialInversion)
class HistorialInversionAdmin(admin.ModelAdmin):
    list_display = ['inversion', 'fecha', 'monto_clp', 'monto_usd']
    list_filter = ['inversion', 'fecha']
    search_fields = ['inversion__nombre', 'inversion__user__username']
    readonly_fields = ['created_at', 'updated_at']
    date_hierarchy = 'fecha'
