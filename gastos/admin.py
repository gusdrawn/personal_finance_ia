from django.contrib import admin

from gastos.models import GastoProgramado, MovimientoManual


@admin.register(MovimientoManual)
class MovimientoManualAdmin(admin.ModelAdmin):
    list_display = ["fecha", "direccion", "descripcion", "monto_clp", "categoria", "user"]
    list_filter = ["direccion", "naturaleza", "moneda", "es_resumen_mensual"]
    search_fields = ["descripcion", "categoria__nombre", "user__username"]
    date_hierarchy = "fecha"


@admin.register(GastoProgramado)
class GastoProgramadoAdmin(admin.ModelAdmin):
    list_display = ["nombre", "monto", "fecha_inicio", "frecuencia", "ahorro_mensual", "user", "activo"]
    list_filter = ["frecuencia", "activo", "user"]
    search_fields = ["nombre", "user__username"]
    list_editable = ["activo"]
    readonly_fields = ["created_at", "updated_at", "ahorro_mensual"]
