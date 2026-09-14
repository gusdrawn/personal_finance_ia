from django import forms
from django.contrib import admin

from .models import (
    ApplePayApiKey,
    ApplePayEvent,
    CategoriaTematica,
    ConfiguracionBanca,
    ConfiguracionNotificaciones,
    ContraparteConocida,
    CredencialBanco,
    ConciliacionEventoExterno,
    DetalleLiquidacionHoum,
    EventoFinancieroExterno,
    ImportacionEstadoCuenta,
    ImportacionFuenteExterna,
    LiquidacionHoum,
    LimiteCategoria,
    MapeoComercio,
    MovimientoBancario,
    PagoNeat,
    RegistroNotificacion,
    SaldoSnapshot,
    SyncLog,
    VinculoPropiedadExterna,
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
        fields = [
            "user", "banco", "conector", "tipo_fuente", "rut", "base_url",
            "sincronizacion_automatica", "hora_sincronizacion", "dia_inicio", "dia_fin",
            "zona_horaria", "detener_al_completar", "reintentar_errores",
            "conector_experimental", "activo",
        ]


@admin.register(CredencialBanco)
class CredencialBancoAdmin(admin.ModelAdmin):
    form = CredencialBancoAdminForm
    list_display = ["banco", "conector", "tipo_fuente", "user", "sincronizacion_automatica", "ultima_sincronizacion", "activo"]
    list_filter = ["tipo_fuente", "sincronizacion_automatica", "activo"]
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
        "fecha", "descripcion", "monto_clp", "moneda", "origen", "estado", "naturaleza",
        "categoria", "esencialidad", "fuente_clasificacion", "por_revisar",
    ]
    list_filter = ["origen", "estado", "naturaleza", "esencialidad", "fuente_clasificacion", "por_revisar", "moneda"]
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
        "moneda",
        "estado",
        "operaciones_nuevas",
        "cupo_disponible_clp",
        "cupo_disponible_usd",
        "created_at",
        "user",
    ]
    list_filter = ["estado", "banco_detectado", "created_at"]
    search_fields = ["nombre_archivo", "hash_archivo", "tarjeta_ultimos4", "user__username"]
    readonly_fields = ["hash_archivo", "created_at", "procesado_at"]


@admin.register(SaldoSnapshot)
class SaldoSnapshotAdmin(admin.ModelAdmin):
    list_display = [
        "producto", "user", "fecha", "deuda_facturada_clp", "deuda_no_facturada_clp",
        "cupo_total_clp", "cupo_utilizado_clp", "cupo_disponible_clp", "cupo_usd",
        "saldo_insoluto",
    ]
    list_filter = ["producto", "fecha"]
    date_hierarchy = "fecha"


@admin.register(SyncLog)
class SyncLogAdmin(admin.ModelAdmin):
    list_display = ["banco", "modo", "iniciado", "exito", "nuevos", "actualizados", "clasificados_ia"]
    list_filter = ["banco", "modo", "exito"]
    readonly_fields = ["iniciado", "finalizado"]


class PagoNeatInline(admin.StackedInline):
    model = PagoNeat
    extra = 0


class LiquidacionHoumInline(admin.StackedInline):
    model = LiquidacionHoum
    extra = 0


@admin.register(EventoFinancieroExterno)
class EventoFinancieroExternoAdmin(admin.ModelAdmin):
    list_display = ["external_id", "fuente", "estado", "fecha_evento", "monto_neto", "requiere_revision", "user"]
    list_filter = ["fuente", "estado", "requiere_revision"]
    search_fields = ["external_id", "user__username"]
    readonly_fields = ["created_at", "updated_at", "metadata"]

    def get_inlines(self, request, obj):
        if obj and obj.fuente == "neat":
            return [PagoNeatInline]
        if obj and obj.fuente == "houm":
            return [LiquidacionHoumInline]
        return []


@admin.register(ImportacionFuenteExterna)
class ImportacionFuenteExternaAdmin(admin.ModelAdmin):
    list_display = ["nombre_archivo", "fuente", "estado", "elementos_detectados", "nuevos", "actualizados", "user", "created_at"]
    list_filter = ["fuente", "estado", "created_at"]
    readonly_fields = ["hash_archivo", "created_at"]


@admin.register(VinculoPropiedadExterna)
class VinculoPropiedadExternaAdmin(admin.ModelAdmin):
    list_display = ["external_property_id", "nombre_externo", "departamento", "user", "activo"]
    list_filter = ["fuente", "activo"]


@admin.register(ConciliacionEventoExterno)
class ConciliacionEventoExternoAdmin(admin.ModelAdmin):
    list_display = ["evento", "movimiento", "rol", "monto_asignado", "confianza", "metodo"]
    list_filter = ["rol", "metodo", "confirmado_manual"]


admin.site.register(DetalleLiquidacionHoum)


@admin.register(ConfiguracionBanca)
class ConfiguracionBancaAdmin(admin.ModelAdmin):
    list_display = ["user", "ventana_meses_ingreso", "umbral_alerta_ocio", "modelo_ia"]


@admin.register(ConfiguracionNotificaciones)
class ConfiguracionNotificacionesAdmin(admin.ModelAdmin):
    list_display = ["user", "telegram_activo", "telegram_token_prefijo", "resumen_diario", "updated_at"]
    list_filter = ["telegram_activo", "resumen_diario", "avisar_gasto_inmediato"]
    readonly_fields = [
        "telegram_bot_token_cifrado",
        "telegram_chat_id_cifrado",
        "telegram_token_prefijo",
        "ultima_prueba_at",
        "ultima_prueba_exitosa",
        "updated_at",
    ]


@admin.register(LimiteCategoria)
class LimiteCategoriaAdmin(admin.ModelAdmin):
    list_display = ["user", "categoria", "monto_mensual_clp", "activo", "updated_at"]
    list_filter = ["activo"]


@admin.register(RegistroNotificacion)
class RegistroNotificacionAdmin(admin.ModelAdmin):
    list_display = ["user", "tipo", "estado", "intentos", "enviado_at", "created_at"]
    list_filter = ["tipo", "estado", "canal"]
    search_fields = ["clave_idempotencia", "user__username"]
    readonly_fields = [
        "user", "movimiento", "tipo", "canal", "clave_idempotencia", "estado",
        "contenido", "error", "intentos", "enviado_at", "created_at", "updated_at",
    ]
