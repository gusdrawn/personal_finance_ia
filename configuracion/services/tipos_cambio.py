"""Selección común de tasas publicadas para reportes financieros."""

from datetime import date

from configuracion.models import TipoCambio


def tipo_cambio_para(fecha=None):
    """Usa una tasa oficial publicada en la fecha de referencia o antes."""
    return TipoCambio.objects.filter(
        fuente="mindicador.cl", fecha__lte=fecha or date.today()
    ).order_by("-fecha", "-id").first()
