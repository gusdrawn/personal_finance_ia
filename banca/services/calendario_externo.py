from calendar import monthrange
from datetime import date

from banca.models import CredencialBanco, EventoFinancieroExterno, VinculoPropiedadExterna


def eventos_externos_calendario(user, year: int, month: int) -> list[dict]:
    """Proyecta Houm/Neat al calendario sin alterar la contabilidad bancaria."""
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])
    result = []
    events = EventoFinancieroExterno.objects.filter(
        user=user,
        periodo=first,
    ).select_related("pago_neat", "liquidacion_houm", "liquidacion_houm__vinculo_propiedad")
    captured_links = set()
    for event in events:
        selected_date = event.fecha_confirmacion or event.fecha_esperada or event.fecha_cargo or event.fecha_evento
        if not selected_date or not first <= selected_date <= last:
            selected_date = first
        if event.fuente == "houm":
            liquidation = event.liquidacion_houm
            name = f"Houm · {liquidation.propiedad_nombre}"
            amount = event.monto_neto
            if liquidation.vinculo_propiedad_id:
                captured_links.add(liquidation.vinculo_propiedad_id)
        else:
            payment = event.pago_neat
            name = f"Neat · {payment.cuenta or payment.destinatario or event.external_id}"
            amount = payment.total_cargado
        result.append({
            "dia_cobro": selected_date.day,
            "fecha": selected_date,
            "nombre": name,
            "monto": amount,
            "tipo": "externo",
            "fuente": event.fuente,
            "estado": event.estado.lower(),
        })

    houm = CredencialBanco.objects.filter(user=user, conector="houm", activo=True).first()
    if houm:
        expected_day = min(max(houm.dia_inicio, 6), houm.dia_fin, last.day)
        for link in VinculoPropiedadExterna.objects.filter(user=user, fuente="houm", activo=True).select_related("departamento"):
            if link.pk in captured_links:
                continue
            result.append({
                "dia_cobro": expected_day,
                "fecha": first.replace(day=expected_day),
                "nombre": f"Houm · {link.departamento.codigo} (estimado {houm.dia_inicio}–{houm.dia_fin})",
                "monto": 0,
                "tipo": "externo",
                "fuente": "houm",
                "estado": "estimado",
            })
    return sorted(result, key=lambda item: (item["dia_cobro"], item["nombre"]))
