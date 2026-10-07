"""Protección del catálogo compartido de entidades financieras."""


def banco_en_uso(banco):
    """Incluye vínculos de todos los usuarios, incluso relaciones ocultas."""
    for relacion in banco._meta.get_fields(include_hidden=True):
        if relacion.auto_created and (relacion.one_to_many or relacion.one_to_one):
            if relacion.related_model._base_manager.filter(
                **{relacion.field.name: banco}
            ).exists():
                return True
    return False
