"""Formularios de gestión de la app banca (credenciales, categorías, parámetros, contrapartes)."""

from __future__ import annotations

from datetime import time as datetime_time
from decimal import Decimal
from django import forms
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bank_sync.registry import available_connectors, connector_tipo
from configuracion.models import Banco
from prestamos.models import Prestamo

from .models import (
    CategoriaTematica,
    ConfiguracionBanca,
    ConfiguracionNotificaciones,
    ContraparteConocida,
    CredencialBanco,
)

_INPUT = (
    "w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-sm "
    "text-slate-200 focus:border-emerald-500 focus:outline-none"
)
_CHECK = "rounded border-slate-600 bg-slate-900 text-emerald-500"

# Nombre legible por connector (para el <select> y para nombrar la institución auto-creada).
CONECTOR_LABELS = {
    "santander": "Santander",
    "falabella": "Banco Falabella",
    "bci": "BCI",
    "scotiabank": "Scotiabank",
    "itau": "Itaú",
    "binance": "Binance",
    "buda": "Buda",
    "cryptomkt": "CryptoMKT",
    "bitcoin_wallet": "Bitcoin (wallet pública)",
    "fintual": "Fintual",
    "neat": "NeatPagos",
    "houm": "Houm",
}


def conector_label(nombre: str) -> str:
    return CONECTOR_LABELS.get(nombre, nombre.replace("_", " ").title())


def listar_conectores() -> list[dict]:
    """[{name, label, tipo}] de todos los connectors registrados, para el dropdown."""
    out = []
    for name in available_connectors():
        out.append(
            {"name": name, "label": conector_label(name), "tipo": connector_tipo(name)}
        )
    # bancos primero, luego el resto, alfabético por label
    out.sort(key=lambda c: (c["tipo"] != "banco", c["label"].lower()))
    return out


class CredencialBancoForm(forms.ModelForm):
    """
    Alta/edición de credencial de una fuente (banco o activo).

    - Bancos: RUT + clave (se cifra con set_clave()).
    - Activos (cripto/inversión): API key + secret (se cifran), o una dirección pública
      para wallets de solo lectura. El `tipo_fuente` se deriva del connector elegido.
    """

    clave = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"class": _INPUT, "placeholder": "••••••••"}),
        help_text="Déjala en blanco para conservar la clave actual.",
    )
    api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(
            attrs={"class": _INPUT, "placeholder": "API key (solo lectura)"},
            render_value=False,
        ),
    )
    api_secret = forms.CharField(
        required=False,
        widget=forms.PasswordInput(
            attrs={"class": _INPUT, "placeholder": "API secret"}, render_value=False
        ),
    )

    class Meta:
        model = CredencialBanco
        fields = [
            "banco", "rut", "conector", "identificador_publico", "base_url",
            "sincronizacion_automatica", "hora_sincronizacion", "dia_inicio", "dia_fin",
            "zona_horaria", "detener_al_completar", "reintentar_errores",
            "conector_experimental", "activo",
        ]
        labels = {
            "rut": "RUT / Usuario",
            "conector": "Fuente",
            "identificador_publico": "Dirección pública (wallet)",
            "base_url": "Endpoint autorizado",
        }
        widgets = {
            "banco": forms.Select(attrs={"class": _INPUT}),
            "rut": forms.TextInput(
                attrs={"class": _INPUT, "placeholder": "12.345.678-9"}
            ),
            "conector": forms.Select(attrs={"class": _INPUT}),
            "identificador_publico": forms.TextInput(
                attrs={"class": _INPUT, "placeholder": "dirección o xpub (BTC)"}
            ),
            "base_url": forms.URLInput(attrs={"class": _INPUT, "placeholder": "https://api.autorizada.example"}),
            "sincronizacion_automatica": forms.CheckboxInput(attrs={"class": _CHECK}),
            "hora_sincronizacion": forms.TimeInput(attrs={"class": _INPUT, "type": "time"}),
            "dia_inicio": forms.NumberInput(attrs={"class": _INPUT, "min": 1, "max": 28}),
            "dia_fin": forms.NumberInput(attrs={"class": _INPUT, "min": 1, "max": 28}),
            "zona_horaria": forms.TextInput(attrs={"class": _INPUT}),
            "detener_al_completar": forms.CheckboxInput(attrs={"class": _CHECK}),
            "reintentar_errores": forms.CheckboxInput(attrs={"class": _CHECK}),
            "conector_experimental": forms.CheckboxInput(attrs={"class": _CHECK}),
            "activo": forms.CheckboxInput(attrs={"class": _CHECK}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["banco"].queryset = Banco.objects.filter(activo=True)
        self.fields["banco"].required = False  # se auto-crea para exchanges/wallets
        self.fields["conector"].choices = [
            (c["name"], c["label"]) for c in listar_conectores()
        ]
        # Compatibilidad: fuentes existentes que no usan programación pueden omitirlos.
        for field_name in ("hora_sincronizacion", "dia_inicio", "dia_fin", "zona_horaria"):
            self.fields[field_name].required = False

    def _tipo(self) -> str:
        try:
            return connector_tipo(self.cleaned_data.get("conector") or "")
        except Exception:
            return "banco"

    def clean(self):
        cleaned = super().clean()
        cleaned["hora_sincronizacion"] = (
            cleaned.get("hora_sincronizacion")
            or getattr(self.instance, "hora_sincronizacion", None)
            or datetime_time(9, 0)
        )
        cleaned["dia_inicio"] = cleaned.get("dia_inicio") or getattr(self.instance, "dia_inicio", 1) or 1
        cleaned["dia_fin"] = cleaned.get("dia_fin") or getattr(self.instance, "dia_fin", 10) or 10
        cleaned["zona_horaria"] = cleaned.get("zona_horaria") or getattr(self.instance, "zona_horaria", "America/Santiago") or "America/Santiago"
        creando = self.instance.pk is None
        tipo = self._tipo()
        conector = cleaned.get("conector") or ""

        inicio, fin = cleaned.get("dia_inicio"), cleaned.get("dia_fin")
        if inicio and fin and inicio > fin:
            self.add_error("dia_fin", "El día final debe ser igual o posterior al inicial.")

        if tipo == "banco":
            if creando and not cleaned.get("clave"):
                self.add_error(
                    "clave", "Debes ingresar la clave al crear la credencial."
                )
            if not cleaned.get("rut"):
                self.add_error("rut", "El banco requiere RUT / usuario.")
        elif tipo in ("pagos", "administracion_inmobiliaria"):
            if cleaned.get("conector_experimental"):
                if not cleaned.get("base_url"):
                    self.add_error("base_url", "El modo experimental requiere un endpoint autorizado.")
                if creando and not cleaned.get("api_key"):
                    self.add_error("api_key", "El modo experimental requiere un token de solo lectura.")
        else:
            # fuentes de activos: wallet por dirección, o exchange por API key+secret
            if conector == "bitcoin_wallet":
                if not cleaned.get("identificador_publico"):
                    self.add_error(
                        "identificador_publico",
                        "La wallet requiere una dirección o xpub.",
                    )
            elif creando and not (cleaned.get("api_key") and cleaned.get("api_secret")):
                self.add_error("api_key", "Ingresa API key y secret (de solo lectura).")
        return cleaned

    def save(self, commit=True):
        creating = self.instance.pk is None
        instance = super().save(commit=False)
        instance.tipo_fuente = self._tipo()

        if creating and instance.conector in ("neat", "houm"):
            if "dia_inicio" not in self.data:
                instance.dia_inicio = 1 if instance.conector == "neat" else 2
            if "dia_fin" not in self.data:
                instance.dia_fin = 1 if instance.conector == "neat" else 10

        # Institución: si no se eligió una, se crea/asocia por el nombre del connector.
        if not instance.banco_id:
            nombre = conector_label(instance.conector)
            instance.banco, _ = Banco.objects.get_or_create(
                nombre=nombre, defaults={"activo": True}
            )

        if self.cleaned_data.get("clave"):
            instance.set_clave(self.cleaned_data["clave"])
        if self.cleaned_data.get("api_key"):
            instance.set_api_key(self.cleaned_data["api_key"])
        if self.cleaned_data.get("api_secret"):
            instance.set_api_secret(self.cleaned_data["api_secret"])

        if commit:
            instance.save()
            from banca.services.fuentes_externas import calcular_proxima_sincronizacion

            instance.proxima_sincronizacion = calcular_proxima_sincronizacion(instance)
            instance.save(update_fields=["proxima_sincronizacion", "updated_at"])
        return instance


class ProgramacionFuenteForm(forms.ModelForm):
    """Edición acotada de la cadencia y del modo read-only experimental."""

    api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"class": _INPUT, "placeholder": "Token de solo lectura"}),
        help_text="Déjalo vacío para conservar el token actual.",
    )
    session_state = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"class": _INPUT, "placeholder": "Cookie/token de sesión renovado"}),
        help_text="Se cifra al guardar. Déjalo vacío para conservar la sesión actual.",
    )

    class Meta:
        model = CredencialBanco
        fields = [
            "sincronizacion_automatica", "hora_sincronizacion", "dia_inicio", "dia_fin",
            "zona_horaria", "detener_al_completar", "reintentar_errores",
            "conector_experimental", "base_url",
        ]
        widgets = {
            "sincronizacion_automatica": forms.CheckboxInput(attrs={"class": _CHECK}),
            "hora_sincronizacion": forms.TimeInput(attrs={"class": _INPUT, "type": "time"}),
            "dia_inicio": forms.NumberInput(attrs={"class": _INPUT, "min": 1, "max": 28}),
            "dia_fin": forms.NumberInput(attrs={"class": _INPUT, "min": 1, "max": 28}),
            "zona_horaria": forms.TextInput(attrs={"class": _INPUT}),
            "detener_al_completar": forms.CheckboxInput(attrs={"class": _CHECK}),
            "reintentar_errores": forms.CheckboxInput(attrs={"class": _CHECK}),
            "conector_experimental": forms.CheckboxInput(attrs={"class": _CHECK}),
            "base_url": forms.URLInput(attrs={"class": _INPUT}),
        }

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("dia_inicio") and cleaned.get("dia_fin") and cleaned["dia_inicio"] > cleaned["dia_fin"]:
            self.add_error("dia_fin", "El día final debe ser igual o posterior al inicial.")
        if cleaned.get("conector_experimental") and not cleaned.get("base_url"):
            self.add_error("base_url", "El modo experimental requiere un endpoint autorizado.")
        try:
            ZoneInfo(cleaned.get("zona_horaria") or "")
        except (ZoneInfoNotFoundError, ValueError):
            self.add_error("zona_horaria", "Usa una zona horaria IANA válida, por ejemplo America/Santiago.")
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.cleaned_data.get("api_key"):
            instance.set_api_key(self.cleaned_data["api_key"])
        if self.cleaned_data.get("session_state"):
            instance.set_sesion(self.cleaned_data["session_state"])
            instance.requiere_reautenticacion = False
            instance.errores_consecutivos = 0
        if commit:
            instance.save()
            from banca.services.fuentes_externas import calcular_proxima_sincronizacion

            instance.proxima_sincronizacion = calcular_proxima_sincronizacion(instance)
            instance.save(update_fields=["proxima_sincronizacion", "updated_at"])
        return instance


class CategoriaTematicaForm(forms.ModelForm):
    class Meta:
        model = CategoriaTematica
        fields = [
            "nombre",
            "aplica_a",
            "esencialidad_defecto",
            "es_interes_o_costo_financiero",
            "activa",
            "orden",
        ]
        widgets = {
            "nombre": forms.TextInput(
                attrs={"class": _INPUT, "placeholder": "Supermercado"}
            ),
            "aplica_a": forms.Select(attrs={"class": _INPUT}),
            "esencialidad_defecto": forms.Select(attrs={"class": _INPUT}),
            "es_interes_o_costo_financiero": forms.CheckboxInput(
                attrs={"class": _CHECK}
            ),
            "activa": forms.CheckboxInput(attrs={"class": _CHECK}),
            "orden": forms.NumberInput(attrs={"class": _INPUT}),
        }


class ConfiguracionBancaForm(forms.ModelForm):
    """Parámetros de presupuesto + proveedor de IA (compatible OpenAI)."""

    umbral_alerta_ocio_pct = forms.IntegerField(
        required=False, min_value=1, max_value=100,
        label="Avisar al usar este porcentaje del disponible para ocio",
        widget=forms.NumberInput(attrs={"class": _INPUT, "min": "1", "max": "100", "step": "1"}),
    )

    ia_api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(
            attrs={"class": _INPUT, "placeholder": "••••••••"}, render_value=False
        ),
        help_text="Déjala en blanco para conservar la API key actual.",
    )

    class Meta:
        model = ConfiguracionBanca
        fields = [
            "ventana_meses_ingreso",
            "umbral_alerta_ocio",
            "presupuesto_mensual_clp",
            "tolerancia_comision_bicicleta",
            "ia_base_url",
            "modelo_ia",
        ]
        labels = {
            "ventana_meses_ingreso": "Meses para promediar ingresos",
            "umbral_alerta_ocio": "Umbral de alerta Ocio (0.80 = 80%)",
            "presupuesto_mensual_clp": "Presupuesto mensual para compras de tarjeta (CLP)",
            "tolerancia_comision_bicicleta": "Tolerancia comisión bicicleta (0.03 = 3%)",
            "ia_base_url": "Base URL del proveedor IA",
            "modelo_ia": "Modelo de IA",
        }
        widgets = {
            "ventana_meses_ingreso": forms.NumberInput(attrs={"class": _INPUT}),
            "umbral_alerta_ocio": forms.NumberInput(
                attrs={"class": _INPUT, "step": "0.01"}
            ),
            "presupuesto_mensual_clp": forms.NumberInput(
                attrs={"class": _INPUT, "min": "0", "step": "1"}
            ),
            "tolerancia_comision_bicicleta": forms.NumberInput(
                attrs={"class": _INPUT, "step": "0.001"}
            ),
            "ia_base_url": forms.TextInput(
                attrs={"class": _INPUT, "placeholder": "https://openrouter.ai/api/v1"}
            ),
            # el <select> de modelo_ia se renderiza en la plantilla desde modelos_disponibles
            "modelo_ia": forms.TextInput(attrs={"class": _INPUT}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["umbral_alerta_ocio"].required = False
        if self.instance and self.instance.pk:
            self.fields["umbral_alerta_ocio_pct"].initial = int(self.instance.umbral_alerta_ocio * 100)

    def clean(self):
        cleaned = super().clean()
        pct = cleaned.get("umbral_alerta_ocio_pct")
        if pct is not None:
            cleaned["umbral_alerta_ocio"] = Decimal(pct) / Decimal("100")
        elif cleaned.get("umbral_alerta_ocio") is None:
            cleaned["umbral_alerta_ocio"] = self.instance.umbral_alerta_ocio if self.instance and self.instance.pk else Decimal("0.80")
        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        clave = self.cleaned_data.get("ia_api_key")
        if clave:
            instance.set_ia_api_key(clave)
        if commit:
            instance.save()
        return instance

    def clean_presupuesto_mensual_clp(self):
        monto = self.cleaned_data["presupuesto_mensual_clp"]
        if monto < 0:
            raise forms.ValidationError("El presupuesto mensual no puede ser negativo.")
        return monto


class ConfiguracionNotificacionesForm(forms.ModelForm):
    telegram_bot_token = forms.CharField(
        required=False,
        widget=forms.PasswordInput(
            attrs={"class": _INPUT, "placeholder": "Token de @BotFather"},
            render_value=False,
        ),
    )
    telegram_chat_id = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": _INPUT, "placeholder": "Chat ID"}),
    )

    class Meta:
        model = ConfiguracionNotificaciones
        fields = [
            "telegram_activo",
            "avisar_gasto_inmediato",
            "monto_minimo_gasto",
            "resumen_diario",
            "hora_resumen_diario",
            "resumen_importacion_pdf",
            "avisar_ocio",
            "avisar_limites_categoria",
            "avisar_vencimientos",
            "dias_anticipacion_vencimientos",
            "avisar_provisional_atrasado",
            "dias_provisional_atrasado",
            "zona_horaria",
        ]
        widgets = {
            "telegram_activo": forms.CheckboxInput(attrs={"class": _CHECK}),
            "avisar_gasto_inmediato": forms.CheckboxInput(attrs={"class": _CHECK}),
            "monto_minimo_gasto": forms.NumberInput(attrs={"class": _INPUT, "min": 0}),
            "resumen_diario": forms.CheckboxInput(attrs={"class": _CHECK}),
            "hora_resumen_diario": forms.TimeInput(attrs={"class": _INPUT, "type": "time"}),
            "resumen_importacion_pdf": forms.CheckboxInput(attrs={"class": _CHECK}),
            "avisar_ocio": forms.CheckboxInput(attrs={"class": _CHECK}),
            "avisar_limites_categoria": forms.CheckboxInput(attrs={"class": _CHECK}),
            "avisar_vencimientos": forms.CheckboxInput(attrs={"class": _CHECK}),
            "dias_anticipacion_vencimientos": forms.NumberInput(attrs={"class": _INPUT, "min": 0, "max": 60}),
            "avisar_provisional_atrasado": forms.CheckboxInput(attrs={"class": _CHECK}),
            "dias_provisional_atrasado": forms.NumberInput(attrs={"class": _INPUT, "min": 1, "max": 180}),
            "zona_horaria": forms.TextInput(attrs={"class": _INPUT}),
        }

    def clean_zona_horaria(self):
        value = self.cleaned_data["zona_horaria"]
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise forms.ValidationError("Usa una zona horaria IANA válida.")
        return value

    def save(self, commit=True):
        instance = super().save(commit=False)
        token = self.cleaned_data.get("telegram_bot_token")
        chat_id = self.cleaned_data.get("telegram_chat_id")
        if token or chat_id:
            instance.set_telegram(token, chat_id)
        if commit:
            instance.save()
        return instance


class ContraparteConocidaForm(forms.ModelForm):
    class Meta:
        model = ContraparteConocida
        fields = ["alias", "rut", "prestamo", "activo"]
        labels = {"alias": "Alias / glosa", "prestamo": "Préstamo vinculado"}
        widgets = {
            "alias": forms.TextInput(attrs={"class": _INPUT, "placeholder": "Juanita"}),
            "rut": forms.TextInput(
                attrs={"class": _INPUT, "placeholder": "12.345.678-9 (opcional)"}
            ),
            "prestamo": forms.Select(attrs={"class": _INPUT}),
            "activo": forms.CheckboxInput(attrs={"class": _CHECK}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Prestamo.objects.filter(activo=True)
        if user is not None:
            qs = qs.filter(user=user)
        self.fields["prestamo"].queryset = qs
        self.fields["prestamo"].required = False
