"""Categoría para traspasos de dinero entre cuentas del mismo usuario."""

from banca.models import CategoriaTematica


CATEGORIA_TRANSFERENCIA_PROPIA = "Transferencias entre mis cuentas"


def categoria_transferencia_propia(user):
    """Disponible también para usuarios existentes, sin sembrar otras categorías."""
    categoria, _ = CategoriaTematica.objects.get_or_create(
        user=user,
        nombre=CATEGORIA_TRANSFERENCIA_PROPIA,
        defaults={
            "aplica_a": "AMBOS",
            "origen": "SISTEMA",
            "esencialidad_defecto": "FIJO",
        },
    )
    return categoria
