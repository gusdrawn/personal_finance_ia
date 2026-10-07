"""
Presupuesto por ingreso promediado (ver §15.4).

    Disponible_Ocio ≈ promedio_ingresos(ventana) − comprometido(FIJO + cuotas del mes)

El gasto Ocio se mide en DEVENGO (fecha de compra). Solo cuenta lo OPERACIONAL.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from banca.models import CategoriaTematica, ConfiguracionBanca, ImportacionEstadoCuenta, LimiteCategoria, MovimientoBancario


def _operacionales(user):
    return MovimientoBancario.objects.filter(
        user=user, producto__user=user, producto__contabilizar=True, naturaleza="OPERACIONAL", por_revisar=False
    ).exclude(estado="DESCARTADO_CIERRE")


def proyeccion_no_facturados(user, year: int | None = None, month: int | None = None) -> dict:
    """Compras TDC aún sin cartola, estimadas para el vencimiento del mes siguiente."""
    from gastos.services import monto_facturado_clp

    hoy = timezone.localdate()
    periodo = date(year or hoy.year, month or hoy.month, 1)
    siguiente = date(periodo.year + (periodo.month == 12),
                     1 if periodo.month == 12 else periodo.month + 1, 1)
    subsiguiente = date(siguiente.year + (siguiente.month == 12),
                        1 if siguiente.month == 12 else siguiente.month + 1, 1)
    pendientes_tdc = MovimientoBancario.objects.filter(
        user=user, producto__user=user, producto__tipo="TDC", producto__contabilizar=True,
        estado__in=("PROVISIONAL", "NO_FACTURADO"), monto__gt=0,
    ).select_related("categoria")
    movimientos = pendientes_tdc.filter(fecha__gte=periodo, fecha__lt=siguiente)
    atrasados = pendientes_tdc.filter(fecha__lt=periodo)
    total = sum((mov.monto_clp for mov in movimientos), Decimal("0"))
    pendientes = sum(1 for mov in movimientos if not mov.categoria_id)
    total_atrasado = sum((mov.monto_clp for mov in atrasados), Decimal("0"))
    estados_proximos = ImportacionEstadoCuenta.objects.filter(
        user=user, producto__user=user, producto__tipo="TDC", producto__contabilizar=True,
        estado="PROCESADO", vigente=True, monto_facturado__isnull=False,
        fecha_vencimiento__gte=siguiente, fecha_vencimiento__lt=subsiguiente,
    )
    importes_facturados = [monto_facturado_clp(estado)[0] for estado in estados_proximos]
    facturado = sum((importe or Decimal("0") for importe in importes_facturados), Decimal("0"))
    sin_conversion = sum(mov.conversion_clp_desconocida for mov in movimientos) + sum(importe is None for importe in importes_facturados)
    total_proyectado = facturado + total
    config = ConfiguracionBanca.objects.filter(user=user).first()
    limite = config.presupuesto_mensual_clp if config else Decimal("0")
    umbral = config.umbral_alerta_ocio if config else Decimal("0.80")
    porcentaje = (total_proyectado * 100 / limite).quantize(Decimal("0.1")) if limite > 0 else Decimal("0")
    return {
        "periodo_compras": periodo,
        "fin_anterior": periodo - timedelta(days=1),
        "fin_compras": siguiente - timedelta(days=1),
        "periodo_estimado": siguiente,
        "hasta_estimado": subsiguiente,
        "total_clp": total,
        "facturado_clp": facturado,
        "total_proyectado_clp": total_proyectado,
        "sin_conversion": sin_conversion,
        "totales_completos": sin_conversion == 0,
        "cantidad": movimientos.count(),
        "sin_categoria": pendientes,
        "atrasados_cantidad": atrasados.count(),
        "atrasados_clp": total_atrasado,
        "presupuesto_clp": limite,
        "porcentaje_usado": porcentaje,
        "alerta": bool(limite > 0 and total_proyectado >= limite * umbral),
        "umbral": umbral * 100,
    }


def _ventana_ingresos(user, ventana_meses, hoy):
    from gastos.services import primer_dia, resumen_gastos

    ventana = max(1, min(int(ventana_meses), 12))
    indice = hoy.year * 12 + hoy.month - ventana
    desde = date(indice // 12, indice % 12 + 1, 1)
    resumen = resumen_gastos(user, desde, primer_dia(hoy), "caja")
    lineas = [item for grupo in resumen["categorias"] for item in grupo["movimientos"]
              if item["direccion"] == "INGRESO" and item["naturaleza"] == "OPERACIONAL"
              and item["fecha"] <= hoy]
    total = sum((item["monto_calculo_clp"] or Decimal("0") for item in lineas), Decimal("0"))
    return (total / Decimal(ventana)).quantize(Decimal("1")), sum(item["conversion_desconocida"] for item in lineas)


def promedio_ingresos(user, ventana_meses: int = 3, hoy: date | None = None) -> Decimal:
    """Caja operacional conocida por todos los meses de la ventana, incluso vacíos."""
    return _ventana_ingresos(user, ventana_meses, hoy or timezone.localdate())[0]


def detalle_promedios_categoria(user, meses: int = 3, hoy: date | None = None) -> list[dict]:
    """Gasto por categoría en meses completos, incluyendo meses sin movimientos."""
    hoy = hoy or timezone.localdate()
    meses = max(1, min(int(meses), 12))
    actual = date(hoy.year, hoy.month, 1)
    periodos = []
    for retroceso in range(meses, 0, -1):
        indice = actual.year * 12 + actual.month - 1 - retroceso
        periodos.append(date(indice // 12, indice % 12 + 1, 1))
    desde = periodos[0]
    siguiente = date(actual.year + (actual.month == 12), 1 if actual.month == 12 else actual.month + 1, 1)
    from gastos.services import resumen_gastos

    resumen = resumen_gastos(user, desde, actual, "consumo")
    montos = defaultdict(Decimal)
    actuales = defaultdict(Decimal)
    for grupo in resumen["categorias"]:
        if grupo["direccion"] != "EGRESO" or not grupo["categoria_id"]:
            continue
        for item in grupo["movimientos"]:
            periodo = item["metadata"].get("periodo_contable") or item["fecha"].replace(day=1)
            valor = item["monto_calculo_clp"] or Decimal("0")
            if periodo == actual:
                actuales[grupo["categoria_id"]] += valor
            else:
                montos[(grupo["categoria_id"], periodo.year, periodo.month)] += valor
    limites = {l.categoria_id: l.monto_mensual_clp for l in LimiteCategoria.objects.filter(user=user, activo=True)}
    resultado = []
    for categoria in CategoriaTematica.objects.filter(user=user, activa=True, aplica_a__in=("EGRESO", "AMBOS")):
        serie = [{"periodo": p, "monto": montos.get((categoria.pk, p.year, p.month), Decimal("0"))} for p in periodos]
        promedio = (sum((fila["monto"] for fila in serie), Decimal("0")) / Decimal(meses)).quantize(Decimal("1"))
        actual_monto = actuales.get(categoria.pk, Decimal("0"))
        limite = limites.get(categoria.pk)
        escala = max([Decimal("1"), promedio, actual_monto, limite or Decimal("0"), *(fila["monto"] for fila in serie)])
        for fila in serie:
            fila["ancho"] = max(0, min(100, round(float(fila["monto"] / escala * 100))))
        resultado.append({
            "categoria": categoria, "meses": serie, "promedio": promedio,
            "actual": actual_monto, "limite": limite,
            "ancho_actual": max(0, min(100, round(float(actual_monto / escala * 100)))),
            "ancho_limite": max(0, min(100, round(float((limite or Decimal("0")) / escala * 100)))),
            "faltante": max((limite or Decimal("0")) - actual_monto, Decimal("0")),
            "superado": bool(limite and actual_monto >= limite),
        })
    return resultado


def _gastos_presupuesto(user, year, month):
    from gastos.services import resumen_gastos

    periodo = date(year, month, 1)
    resumen = resumen_gastos(user, periodo, periodo, "consumo")
    lineas = [item for grupo in resumen["categorias"] for item in grupo["movimientos"]
              if item["direccion"] == "EGRESO" and item["naturaleza"] == "OPERACIONAL"]
    comprometido = ocio = Decimal("0")
    for item in lineas:
        valor = item["monto_calculo_clp"] or Decimal("0")
        if item["esencialidad"] == "FIJO" or (item["metadata"].get("cuota_total") or 0) > 1:
            comprometido += valor
        elif item["esencialidad"] == "OCIO":
            ocio += valor
    return comprometido, ocio, sum(item["conversion_desconocida"] for item in lineas)


def comprometido_mes(user, year: int, month: int) -> Decimal:
    """Detalle compartido de consumo fijo y cuotas, incluido lo registrado manualmente."""
    return _gastos_presupuesto(user, year, month)[0]


def ocio_gastado_mes(user, year: int, month: int) -> Decimal:
    """Consumo de ocio neto de devoluciones, sin cuotas ni activos recuperables."""
    return _gastos_presupuesto(user, year, month)[1]


def resumen_presupuesto(user, year: int | None = None, month: int | None = None) -> dict:
    hoy = timezone.localdate()
    year = year or hoy.year
    month = month or hoy.month
    cfg = ConfiguracionBanca.objects.filter(user=user).first()
    ventana = cfg.ventana_meses_ingreso if cfg else 3
    umbral = cfg.umbral_alerta_ocio if cfg else Decimal("0.80")

    from gastos.services import siguiente_mes

    corte = min(hoy, siguiente_mes(date(year, month, 1)) - timedelta(days=1))
    ingreso_prom, ingresos_sin_conversion = _ventana_ingresos(user, ventana, corte)
    comprometido, ocio, gastos_sin_conversion = _gastos_presupuesto(user, year, month)
    sin_conversion = ingresos_sin_conversion + gastos_sin_conversion
    disponible = ingreso_prom - comprometido
    pct = (ocio / disponible) if disponible > 0 else Decimal("0")

    return {
        "year": year,
        "month": month,
        "ventana_meses": max(1, min(int(ventana), 12)),
        "sin_conversion": sin_conversion,
        "totales_completos": sin_conversion == 0,
        "promedio_ingresos": ingreso_prom,
        "comprometido": comprometido,
        "disponible_ocio": disponible,
        "ocio_gastado": ocio,
        "ocio_restante": disponible - ocio,
        "porcentaje_usado": (pct * 100).quantize(Decimal("0.1")),
        "alerta": bool(disponible > 0 and pct >= umbral),
        "umbral": (umbral * 100),
    }
