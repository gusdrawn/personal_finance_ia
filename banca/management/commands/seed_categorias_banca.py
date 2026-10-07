"""
Siembra un set de categorías temáticas por defecto (con su esencialidad FIJO/OCIO).

Sin categorías sembradas, el presupuesto FIJO/OCIO y la IA quedan flojos: la IA crearía
categorías ad-hoc todas en OCIO. Este comando es idempotente (get_or_create por nombre).

    python manage.py seed_categorias_banca               # todos los usuarios
    python manage.py seed_categorias_banca --user gustavo
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from banca.models import CategoriaTematica
from banca.services.transferencias import CATEGORIA_TRANSFERENCIA_PROPIA

# (nombre, esencialidad, es_interes_o_costo_financiero)
CATEGORIAS_DEFECTO = [
    ("Arriendo / Hipoteca", "FIJO", False),
    ("Financiamiento / Hipoteca", "FIJO", False),
    ("Financiamiento / Pago de TDC", "FIJO", False),
    ("Pagos por cuenta de terceros", "FIJO", False),
    ("Bicicletas", "FIJO", False),
    (CATEGORIA_TRANSFERENCIA_PROPIA, "FIJO", False),
    ("Servicios básicos", "FIJO", False),
    ("Telefonía / Internet", "FIJO", False),
    ("Seguros", "FIJO", False),
    ("Salud", "FIJO", False),
    ("Educación", "FIJO", False),
    ("Impuestos", "FIJO", False),
    ("Supermercado", "FIJO", False),
    ("Transporte", "FIJO", False),
    ("Suscripciones", "OCIO", False),
    ("Restaurantes", "OCIO", False),
    ("Entretenimiento", "OCIO", False),
    ("Compras / Retail", "OCIO", False),
    ("Tecnología", "OCIO", False),
    ("Viajes", "OCIO", False),
    ("Intereses y comisiones", "FIJO", True),
    ("Otros", "OCIO", False),
]


def sembrar(user) -> int:
    """Crea las categorías por defecto que falten para `user`. Devuelve cuántas creó."""
    creadas = 0
    for orden, (nombre, esencialidad, es_interes) in enumerate(CATEGORIAS_DEFECTO):
        _, created = CategoriaTematica.objects.get_or_create(
            user=user,
            nombre=nombre,
            defaults={
                "esencialidad_defecto": esencialidad,
                "es_interes_o_costo_financiero": es_interes,
                "aplica_a": "AMBOS" if nombre in {"Bicicletas", CATEGORIA_TRANSFERENCIA_PROPIA} else "EGRESO",
                "origen": "SISTEMA",
                "orden": orden,
            },
        )
        if created:
            creadas += 1
    return creadas


class Command(BaseCommand):
    help = "Siembra las categorías temáticas por defecto (idempotente)."

    def add_arguments(self, parser):
        parser.add_argument("--user", type=str, default=None, help="username (opcional; por defecto todos)")

    def handle(self, *args, **opts):
        User = get_user_model()
        qs = User.objects.all()
        if opts["user"]:
            qs = qs.filter(username=opts["user"])
        if not qs.exists():
            self.stderr.write(self.style.WARNING("No hay usuarios que coincidan."))
            return
        for user in qs:
            creadas = sembrar(user)
            self.stdout.write(self.style.SUCCESS(f"{user.username}: {creadas} categorías nuevas."))
