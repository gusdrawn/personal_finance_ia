from django.contrib import admin
from .models import Departamento, Arrendatario, CreditoHipotecario, Estacionamiento


@admin.register(Departamento)
class DepartamentoAdmin(admin.ModelAdmin):
    list_display = ['codigo', 'piso', 'metros_cuadrados', 'valor_actual_uf', 'user', 'activo']
    list_filter = ['user', 'activo', 'created_at']
    search_fields = ['codigo', 'piso', 'user__username']
    list_editable = ['activo']
    readonly_fields = ['created_at', 'updated_at', 'ganancia_capital_uf', 'porcentaje_ganancia_capital']
    fieldsets = (
        ('Información Básica', {'fields': ('user', 'codigo', 'piso', 'metros_cuadrados')}),
        ('Valuación', {'fields': ('valor_compra_uf', 'valor_actual_uf', 'avaluo_fiscal')}),
        ('Ganancias', {'fields': ('ganancia_capital_uf', 'porcentaje_ganancia_capital'), 'classes': ('collapse',)}),
        ('Estacionamientos', {'fields': ('tiene_estacionamientos', 'numero_bodegas')}),
        ('Estado', {'fields': ('activo',)}),
        ('Notas', {'fields': ('notas',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )


@admin.register(Arrendatario)
class ArrendatarioAdmin(admin.ModelAdmin):
    list_display = ['nombre', 'rut', 'departamento', 'monto_arriendo_clp', 'fecha_inicio_contrato', 'activo']
    list_filter = ['departamento', 'activo', 'fecha_inicio_contrato']
    search_fields = ['nombre', 'rut', 'departamento__codigo']
    list_editable = ['activo']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(CreditoHipotecario)
class CreditoHipotecarioAdmin(admin.ModelAdmin):
    list_display = ['departamento', 'banco', 'tasa_anual', 'plazo_anos', 'saldo_actual_uf', 'cuotas_restantes']
    list_filter = ['banco', 'tipo_tasa', 'activo', 'dfl2']
    search_fields = ['departamento__codigo', 'banco__nombre']
    readonly_fields = ['created_at', 'updated_at', 'cuotas_restantes', 'calcular_amortizacion_mensual']
    fieldsets = (
        ('Crédito', {'fields': ('departamento', 'banco', 'cae')}),
        ('Términos', {'fields': ('monto_original_uf', 'tasa_anual', 'plazo_anos', 'tipo_tasa', 'dfl2')}),
        ('Cuotas', {'fields': ('cuota_uf', 'saldo_actual_uf', 'cuotas_pagadas', 'cuotas_totales', 'cuotas_restantes')}),
        ('Estado', {'fields': ('activo',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )


@admin.register(Estacionamiento)
class EstacionamientoAdmin(admin.ModelAdmin):
    list_display = ['numero', 'departamento', 'asignado_a_arrendatario', 'arrendatario']
    list_filter = ['departamento', 'asignado_a_arrendatario']
    search_fields = ['departamento__codigo', 'numero', 'arrendatario__nombre']
