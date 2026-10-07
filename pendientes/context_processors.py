from datetime import date, timedelta

from .models import OcurrenciaPendiente


def pendientes_header(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {"pendientes_header_count": 0}
    return {
        "pendientes_header_count": OcurrenciaPendiente.objects.filter(
            regla__user=request.user,
            regla__activa=True,
            estado="PENDIENTE",
            fecha_vencimiento__gte=date.today() - timedelta(days=30),
            fecha_vencimiento__lte=date.today() + timedelta(days=7),
        ).count()
    }
