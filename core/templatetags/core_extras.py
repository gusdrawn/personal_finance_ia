from django import template
from django.utils import timezone
from decimal import Decimal, InvalidOperation

register = template.Library()


@register.filter
def monto_cobro(value, moneda="CLP"):
    """Usa el mismo formato monetario en la revisión y en el texto conservado."""
    from patrimonio.services.cuentas_por_cobrar import formatear_monto

    try:
        monto = Decimal(str(value or "0"))
        return formatear_monto(monto, moneda) if monto.is_finite() else ""
    except (InvalidOperation, ValueError, TypeError):
        return ""

@register.filter
def multiply(value, arg):
    try:
        return float(value) * float(arg)
    except (ValueError, TypeError):
        return 0

@register.filter
def divide(value, arg):
    try:
        return float(value) / float(arg)
    except (ValueError, TypeError, ZeroDivisionError):
        return 0

@register.filter
def subtract(value, arg):
    try:
        return float(value) - float(arg)
    except (ValueError, TypeError):
        return 0


@register.filter(name='split')
def split(value, arg):
    return value.split(arg)


@register.filter
def tiempo_desde(value):
    """Devuelve una fecha relativa breve y localizada para la interfaz."""
    if not value:
        return ""

    try:
        if timezone.is_naive(value):
            value = timezone.make_aware(value, timezone.get_current_timezone())
        segundos = max(0, int((timezone.now() - value).total_seconds()))
    except (AttributeError, TypeError, ValueError):
        return ""

    if segundos < 60:
        return "hace unos segundos"

    minutos = segundos // 60
    if minutos < 60:
        return f"hace {minutos} minuto{'s' if minutos != 1 else ''}"

    horas = minutos // 60
    if horas < 24:
        return f"hace {horas} hora{'s' if horas != 1 else ''}"

    dias = horas // 24
    if dias <= 30:
        return f"hace {dias} día{'s' if dias != 1 else ''}"

    meses = dias // 30
    if meses < 12:
        return f"hace {meses} mes{'es' if meses != 1 else ''}"

    anos = dias // 365
    return f"hace {anos} año{'s' if anos != 1 else ''}"
