from django.apps import AppConfig


class PrestamosConfig(AppConfig):
    name = 'prestamos'

    def ready(self):
        import prestamos.signals  # noqa: F401
