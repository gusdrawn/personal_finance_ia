from django.db import migrations
from django.db.models import Q


def separar_categorias(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Categoria = apps.get_model("banca", "CategoriaTematica")
    Evento = apps.get_model("banca", "EventoFinancieroExterno")
    Conciliacion = apps.get_model("banca", "ConciliacionEventoExterno")
    Movimiento = apps.get_model("banca", "MovimientoBancario")

    for user in User.objects.all().iterator():
        hipoteca, _ = Categoria.objects.get_or_create(
            user_id=user.pk,
            nombre="Financiamiento / Hipoteca",
            defaults={
                "aplica_a": "EGRESO",
                "origen": "SISTEMA",
                "esencialidad_defecto": "FIJO",
            },
        )
        Categoria.objects.get_or_create(
            user_id=user.pk,
            nombre="Pagos por cuenta de terceros",
            defaults={
                "aplica_a": "EGRESO",
                "origen": "SISTEMA",
                "esencialidad_defecto": "FIJO",
            },
        )
        eventos = Evento.objects.filter(
            user_id=user.pk,
            fuente="neat",
            categoria_es_manual=False,
        ).filter(
            Q(pago_neat__cuenta__icontains="hipotec")
            | Q(pago_neat__destinatario__icontains="hipotec")
        )
        eventos_ids = list(eventos.values_list("id", flat=True))
        eventos.update(
            categoria_id=hipoteca.pk,
            fuente_clasificacion="REGLA",
            confianza_ia=None,
        )
        movimientos_ids = Conciliacion.objects.filter(
            evento_id__in=eventos_ids
        ).values_list("movimiento_id", flat=True)
        Movimiento.objects.filter(
            id__in=movimientos_ids,
            user_id=user.pk,
            categoria_es_manual=False,
        ).update(
            categoria_id=hipoteca.pk,
            fuente_clasificacion="REGLA",
            confianza_ia=None,
        )


class Migration(migrations.Migration):
    dependencies = [("banca", "0014_gastos_unificados")]

    operations = [
        migrations.RunPython(separar_categorias, migrations.RunPython.noop),
    ]
