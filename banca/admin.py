from django import forms
from django.contrib import admin

from .models import (
    ApplePayApiKey,
    ApplePayEvent,
    CategoriaTematica,
    ConfiguracionBanca,
    ContraparteConocida,
    CredencialBanco,
    ImportacionEstadoCuenta,
    MapeoComercio,
    MovimientoBancario,
    SaldoSnapshot,
    SyncLog,
)


@admin.register(ApplePayApiKey)
class ApplePayApiKeyAdmin(admin.ModelAdmin):
    list_display = ["nombre", "prefijo", "user", "activo", "ultimo_uso_at", "created_at"]
    list_filter = ["activo", "created_at"]
    search_fields = ["nombre", "prefijo", "user__username"]
    readonly_fields = ["prefijo", "clave_hash", "ultimo_uso_at", "created_at", "updated_at"]


@admin.register(ApplePayEvent)
class ApplePayEventAdmin(admin.ModelAdmin):
    list_display = ["event_id", "user", "estado", "movimiento", "received_at", "processed_at"]
    list_filter = ["estado", "received_at"]
    search_fields = ["event_id", "user__username", "movimiento__descripcion"]
    readonly_fields = ["payload_hash", "received_at", "processed_at", "respuesta"]


class CredencialBancoAdminForm(forms.ModelForm):
    """Permite escribir la clave en texto; se cifra con set_clave() al guardar."""

    clave = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text="Déjala en blanco para conservar la clave actual.",
    )

    class Meta:
        model = CredencialBanco
        fields = ["user", "banco", "conector", "rut", "activo"]


@admin.register(CredencialBanco)
class CredencialBancoAdmin(admin.ModelAdmin):
    form = CredencialBancoAdminForm
    list_display = ["banco", "rut", "conector", "user", "activo"]
    list_filter = ["banco", "activo"]
    search_fields = ["rut", "user__username"]

    def save_model(self, request, obj, form, change):
        clave = form.cleaned_data.get("clave")
        if clave:
            obj.set_clave(clave)
        super().save_model(request, obj, form, change)


@admin.register(CategoriaTematica)
class CategoriaTematicaAdmin(admin.ModelAdmin):
    list_display = ["nombre", "esencialidad_defecto", "es_interes_o_costo_financiero", "activa", "orden", "user"]
    list_filter = ["esencialidad_defecto", "activa"]
    list_editable = ["esencialidad_defecto", "orden", "activa"]
    search_fields = ["nombre"]


@admin.register(MapeoComercio)
class MapeoComercioAdmin(admin.ModelAdmin):
    list_display = ["patron", "categoria", "origen", "hits", "user"]
    list_filter = ["origen"]
    search_fields = ["patron"]


@admin.register(ContraparteConocida)
class ContraparteConocidaAdmin(admin.ModelAdmin):
    list_display = ["alias", "rut", "prestamo", "activo", "user"]
    search_fields = ["alias", "rut"]


@admin.register(MovimientoBancario)
class MovimientoBancarioAdmin(admin.ModelAdmin):
    list_display = [
        "fecha", "descripcion", "monto_clp", "moneda", "estado", "naturaleza",
        "categoria", "esencialidad", "fuente_clasificacion", "por_revisar",
    ]
    list_filter = ["estado", "naturaleza", "esencialidad", "fuente_clasificacion", "por_revisar", "moneda"]
    search_fields = ["descripcion", "hash_externo"]
    date_hierarchy = "fecha"
    list_select_related = ["categoria", "producto"]
    readonly_fields = ["hash_externo", "created_at", "updated_at"]
    list_per_page = 50


@admin.register(ImportacionEstadoCuenta)
class ImportacionEstadoCuentaAdmin(admin.ModelAdmin):
    list_display = [
        "nombre_archivo",
        "banco_detectado",
        "tarjeta_ultimos4",
        "estado",
        "operaciones_nuevas",
        "created_at",
        "user",
    ]
    list_filter = ["estado", "banco_detectado", "created_at"]
    search_fields = ["nombre_archivo", "hash_archivo", "tarjeta_ultimos4", "user__username"]
    readonly_fields = ["hash_archivo", "created_at", "procesado_at"]


@admin.register(SaldoSnapshot)
class SaldoSnapshotAdmin(admin.ModelAdmin):
    list_display = ["producto", "fecha", "deuda_facturada_clp", "deuda_no_facturada_clp", "cupo_usd", "saldo_insoluto"]
    list_filter = ["producto", "fecha"]
    date_hierarchy = "fecha"


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    list_display = ["banco", "modo", "iniciado", "exito", "nuevos", "actualizados", "clasificados_ia"]
    list_filter = ["banco", "modo", "exito"]
    readonly_fields = ["iniciado", "finalizado"]


@admin.register(ConfiguracionBanca)
class ConfiguracionBancaAdmin(admin.ModelAdmin):
    list_display = ["user", "ventana_meses_ingreso", "umbral_alerta_ocio", "modelo_ia"]
