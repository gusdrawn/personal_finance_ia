from django.apps import AppConfig


class BancaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "banca"
    verbose_name = "Banca (integración bancaria)"

    def ready(self):
        # Conecta señales (sync de pasivos hacia patrimonio, etc.)
        try:
            import banca.signals  # noqa: F401
        except Exception:
            pass
