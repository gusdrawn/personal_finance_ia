"""Expone la app Celery al importar el paquete del proyecto (necesario para autodiscover)."""

from .celery import app as celery_app

__all__ = ("celery_app",)
