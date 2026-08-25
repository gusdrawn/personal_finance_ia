"""Formularios de gestión de la app banca (credenciales, categorías, parámetros, contrapartes)."""

from __future__ import annotations

from django import forms

from bank_sync.registry import available_connectors, connector_tipo
from configuracion.models import Banco
from prestamos.models import Prestamo

from .models import (
    CategoriaTematica,
    ConfiguracionBanca,
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
        fields = ["banco", "rut", "conector", "identificador_publico", "activo"]
        labels = {
            "rut": "RUT / Usuario",
            "conector": "Fuente",
            "identificador_publico": "Dirección pública (wallet)",
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
            "activo": forms.CheckboxInput(attrs={"class": _CHECK}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["banco"].queryset = Banco.objects.filter(activo=True)
        self.fields["banco"].required = False  # se auto-crea para exchanges/wallets
        self.fields["conector"].choices = [
            (c["name"], c["label"]) for c in listar_conectores()
        ]

    def _tipo(self) -> str:
        try:
            return connector_tipo(self.cleaned_data.get("conector") or "")
        except Exception:
            return "banco"

    def clean(self):
        cleaned = super().clean()
        creando = self.instance.pk is None
        tipo = self._tipo()
        conector = cleaned.get("conector") or ""

        if tipo == "banco":
            if creando and not cleaned.get("clave"):
                self.add_error(
                    "clave", "Debes ingresar la clave al crear la credencial."
                )
            if not cleaned.get("rut"):
                self.add_error("rut", "El banco requiere RUT / usuario.")
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
        instance = super().save(commit=False)
        instance.tipo_fuente = self._tipo()

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
        return instance


class CategoriaTematicaForm(forms.ModelForm):
    class Meta:
        model = CategoriaTematica
        fields = [
            "nombre",
            "esencialidad_defecto",
            "es_interes_o_costo_financiero",
            "activa",
            "orden",
        ]
        widgets = {
            "nombre": forms.TextInput(
                attrs={"class": _INPUT, "placeholder": "Supermercado"}
            ),
            "esencialidad_defecto": forms.Select(attrs={"class": _INPUT}),
            "es_interes_o_costo_financiero": forms.CheckboxInput(
                attrs={"class": _CHECK}
            ),
            "activa": forms.CheckboxInput(attrs={"class": _CHECK}),
            "orden": forms.NumberInput(attrs={"class": _INPUT}),
        }


class ConfiguracionBancaForm(forms.ModelForm):
    """Parámetros de presupuesto + proveedor de IA (compatible OpenAI)."""

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
            "tolerancia_comision_bicicleta",
            "ia_base_url",
            "modelo_ia",
        ]
        labels = {
            "ventana_meses_ingreso": "Meses para promediar ingresos",
            "umbral_alerta_ocio": "Umbral de alerta Ocio (0.80 = 80%)",
            "tolerancia_comision_bicicleta": "Tolerancia comisión bicicleta (0.03 = 3%)",
            "ia_base_url": "Base URL del proveedor IA",
            "modelo_ia": "Modelo de IA",
        }
        widgets = {
            "ventana_meses_ingreso": forms.NumberInput(attrs={"class": _INPUT}),
            "umbral_alerta_ocio": forms.NumberInput(
                attrs={"class": _INPUT, "step": "0.01"}
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

    def save(self, commit=True):
        instance = super().save(commit=False)
        clave = self.cleaned_data.get("ia_api_key")
        if clave:
            instance.set_ia_api_key(clave)
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
