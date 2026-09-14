from django import template

register = template.Library()

@register.filter(name='split')
def split(value, arg):
    return value.split(arg)


@register.filter(name='get_item')
def get_item(value, key):
    """Permite leer un diccionario en templates sin exponer lógica de negocio."""
    if isinstance(value, dict):
        return value.get(key, '')
    return ''
