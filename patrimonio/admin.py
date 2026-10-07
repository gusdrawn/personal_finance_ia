from django import forms
from django.contrib import admin
from configuracion.services.tipos_cambio import tipo_cambio_para
from .models import (
    AbonoCuentaPorCobrar,
    Activo,
    CuentaPorCobrar,
    DetalleCuentaPorCobrar,
    HistorialActivo,
    Pasivo,
    SnapshotPatrimonio,
    MiniSesion,
    LineaMiniSesion,
)


class SaldoOriginalAdminForm(forms.ModelForm):
    def clean(self):
        datos = super().clean()
        if "monto_original" not in self.fields:
            return datos
        monto, moneda = datos.get("monto_original"), datos.get("moneda_original")
        if monto is None and (moneda or not self.instance.pk):
            self.add_error("monto_original", "Indica el monto en su moneda original.")
        if not moneda and (monto is not None or not self.instance.pk):
            self.add_error("moneda_original", "Selecciona la moneda del saldo.")
        if monto is not None and monto < 0:
            self.add_error("monto_original", "El saldo no puede ser negativo.")
        return datos


class SaldoOriginalAdminMixin:
    form = SaldoOriginalAdminForm

    def get_readonly_fields(self, request, obj=None):
        campos = super().get_readonly_fields(request, obj)
        automatico = obj and (
            getattr(obj, "sincronizado_banca", False)
            or getattr(obj, "fuente_externa", "")
            or (isinstance(obj, Activo) and hasattr(obj, "cuenta_por_cobrar"))
            or (isinstance(obj, Activo) and hasattr(obj, "prestamo_recuperable"))
        )
        return (*campos, "monto_original", "moneda_original") if automatico else campos

    def save_model(self, request, obj, form, change):
        if "monto_original" in form.fields and obj.monto_original is not None and obj.moneda_original:
            obj.asignar_saldo_original(obj.monto_original, obj.moneda_original, tipo_cambio_para())
        super().save_model(request, obj, form, change)


@admin.register(Activo)
class ActivoAdmin(SaldoOriginalAdminMixin, admin.ModelAdmin):
    list_display = ['nombre', 'tipo', 'horizonte_temporal', 'es_liquido', 'monto_clp', 'monto_usd', 'activo', 'user']
    list_filter = ['tipo', 'horizonte_temporal', 'es_liquido', 'activo', 'user', 'created_at']
    search_fields = ['nombre', 'user__username']
    list_editable = ['activo', 'es_liquido']
    readonly_fields = ['created_at', 'updated_at', 'monto_clp', 'monto_usd', 'requiere_revision_moneda']
    fieldsets = (
        ('Información', {'fields': ('user', 'nombre', 'tipo', 'horizonte_temporal', 'es_liquido', 'activo')}),
        ('Saldo original', {'fields': ('monto_original', 'moneda_original', 'requiere_revision_moneda')}),
        ('Equivalentes de referencia', {'fields': ('monto_clp', 'monto_usd')}),
        ('Relacionado', {'fields': ('departamento',)}),
        ('Notas', {'fields': ('notas',)}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )


@admin.register(CuentaPorCobrar)
class CuentaPorCobrarAdmin(admin.ModelAdmin):
    list_display = ["nombre", "moneda", "saldo_pendiente", "activo", "user"]
    list_filter = ["moneda", "activo", "user"]
    search_fields = ["nombre", "descripcion", "user__username"]
    readonly_fields = ["activo_patrimonial", "created_at", "updated_at"]


@admin.register(DetalleCuentaPorCobrar)
class DetalleCuentaPorCobrarAdmin(admin.ModelAdmin):
    list_display = ["cuenta", "periodo", "descripcion", "monto", "origen", "monto_manual"]
    list_filter = ["origen", "monto_manual", "periodo"]
    search_fields = ["cuenta__nombre", "descripcion"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(AbonoCuentaPorCobrar)
class AbonoCuentaPorCobrarAdmin(admin.ModelAdmin):
    list_display = ["cuenta", "detalle", "movimiento", "fecha", "monto", "descripcion"]
    list_filter = ["fecha"]
    search_fields = ["cuenta__nombre", "descripcion"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(HistorialActivo)
class HistorialActivoAdmin(admin.ModelAdmin):
    list_display = ['activo', 'fecha', 'monto_clp', 'monto_usd']
    list_filter = ['activo', 'fecha']
    search_fields = ['activo__nombre', 'activo__user__username']
    readonly_fields = ['created_at', 'updated_at']
    date_hierarchy = 'fecha'


@admin.register(Pasivo)
class PasivoAdmin(SaldoOriginalAdminMixin, admin.ModelAdmin):
    list_display = ['nombre', 'tipo', 'monto_clp', 'monto_usd', 'activo', 'user']
    list_filter = ['tipo', 'activo', 'user', 'created_at']
    search_fields = ['nombre', 'user__username']
    list_editable = ['activo']
    readonly_fields = ['created_at', 'updated_at', 'monto_clp', 'monto_usd', 'requiere_revision_moneda', 'sincronizado_banca']


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
