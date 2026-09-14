from django import forms

from .models import AltcoinProject, EvidenceRecord, InstitutionalEntity, ProjectEntityRelationship


FIELD_CLASS = "w-full rounded-xl border border-slate-700 bg-slate-900/70 px-3 py-2.5 text-sm text-white outline-none focus:border-emerald-500"


class DiscoverySearchForm(forms.Form):
    SOURCE_CHOICES = (
        ("COINGECKO_SEARCH", "Catálogo amplio CoinGecko"),
        ("ROBINHOOD_PUBLIC", "Catálogo público Robinhood"),
        ("WEB_SEARCH", "Internet (Brave Search)"),
    )
    query = forms.CharField(
        label="Buscar proyecto, ticker o temática",
        max_length=220,
        required=False,
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "Ej. DePIN, MEGA, privacidad"}),
    )
    source = forms.ChoiceField(
        label="Fuente",
        choices=SOURCE_CHOICES,
        initial="COINGECKO_SEARCH",
        widget=forms.Select(attrs={"class": FIELD_CLASS}),
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("source") != "ROBINHOOD_PUBLIC" and not (cleaned.get("query") or "").strip():
            self.add_error("query", "Escribe qué quieres buscar.")
        return cleaned


class InstitutionalResearchForm(forms.Form):
    project = forms.ModelChoiceField(
        label="Proyecto",
        queryset=AltcoinProject.objects.none(),
        widget=forms.Select(attrs={"class": FIELD_CLASS}),
    )
    entity_name = forms.CharField(
        label="Institución, persona o empresa",
        max_length=220,
        widget=forms.TextInput(attrs={"class": FIELD_CLASS, "placeholder": "Ej. Coinbase Ventures"}),
    )
    entity_type = forms.ChoiceField(
        label="Tipo de entidad",
        choices=InstitutionalEntity.ENTITY_CHOICES,
        initial="UNKNOWN",
        widget=forms.Select(attrs={"class": FIELD_CLASS}),
    )
    relationship_type = forms.ChoiceField(
        label="Tipo de relación",
        choices=ProjectEntityRelationship.RELATION_CHOICES,
        widget=forms.Select(attrs={"class": FIELD_CLASS}),
    )
    source_url = forms.URLField(
        label="URL de evidencia",
        max_length=1000,
        widget=forms.URLInput(attrs={"class": FIELD_CLASS, "placeholder": "https://..."}),
    )
    title = forms.CharField(
        label="Título de la fuente",
        max_length=300,
        required=False,
        widget=forms.TextInput(attrs={"class": FIELD_CLASS}),
    )
    excerpt = forms.CharField(
        label="Fragmento que respalda la relación",
        max_length=2000,
        required=False,
        widget=forms.Textarea(attrs={"class": f"{FIELD_CLASS} min-h-24", "rows": 3}),
    )
    reliability_tier = forms.ChoiceField(
        label="Calidad de la fuente",
        choices=EvidenceRecord.RELIABILITY_CHOICES,
        initial="D_UNVERIFIED",
        widget=forms.Select(attrs={"class": FIELD_CLASS}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["project"].queryset = AltcoinProject.objects.filter(active=True, in_universe=True).order_by("universe_position", "name")
