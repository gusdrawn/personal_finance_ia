from django.contrib import admin

from .models import AsignacionPagoPendiente, OcurrenciaPendiente, ReglaPendiente


@admin.register(ReglaPendiente)
class ReglaPendienteAdmin(admin.ModelAdmin):
    list_display = ("nombre", "user", "tipo", "departamento", "periodicidad", "activa", "automatica")
    list_filter = ("tipo", "periodicidad", "activa", "automatica")
    search_fields = ("nombre", "clave_fuente", "user__username")


@admin.register(OcurrenciaPendiente)
class OcurrenciaPendienteAdmin(admin.ModelAdmin):
    list_display = ("regla", "fecha_vencimiento", "estado", "monto_esperado", "fecha_real")
    list_filter = ("estado", "regla__tipo")
    search_fields = ("regla__nombre", "regla__user__username")


@admin.register(AsignacionPagoPendiente)
class AsignacionPagoPendienteAdmin(admin.ModelAdmin):
    list_display = ("ocurrencia", "monto_asignado", "movimiento_bancario", "movimiento_manual", "referencia_pago")
