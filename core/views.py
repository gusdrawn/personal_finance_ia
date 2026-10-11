from django.shortcuts import get_object_or_404, render, redirect
from django.http import JsonResponse
from django.conf import settings
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.views.decorators.http import require_http_methods
from django.db.models import Sum, Q, F, Avg, Prefetch
from django.urls import reverse
from django.utils import timezone
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
import json

from gastos.models import GastoProgramado, MovimientoManual
from gastos.services import resumen_gastos
from patrimonio.models import (
    AbonoCuentaPorCobrar,
    Activo,
    CuentaPorCobrar,
    CalculoCuentaPorCobrar,
    DetalleCuentaPorCobrar,
    HistorialActivo,
    PartidaRevisionCobro,
    normalizar_monto,
    Pasivo,
    SnapshotPatrimonio,
)
from patrimonio.services.valoracion import resumen_patrimonio
from patrimonio.services.sincronizacion import origen_activo, origenes_activos
from departamentos.models import Departamento, Estacionamiento, Arrendatario, CreditoHipotecario
from configuracion.models import TipoCambio, Banco, CredencialIntegracion, Producto
from configuracion.services.integrations import get_integration_api_key
from configuracion.services.bancos import banco_en_uso
from configuracion.services.tipos_cambio import tipo_cambio_para
from banca.forms import ConfiguracionNotificacionesForm
from banca.models import (
    ApplePayApiKey,
    CategoriaTematica,
    ConfiguracionBanca,
    ConfiguracionNotificaciones,
    EventoFinancieroExterno,
    LimiteCategoria,
    MovimientoBancario,
    RegistroNotificacion,
    VinculoPropiedadExterna,
)
from banca.services.analisis import cupos_tdc, vencimientos_tdc
from banca.services.fuentes_externas import huella_pago_neat
from banca.services.notificador import probar_telegram
from banca.services.houm_analytics import resumen_houm_departamentos
from core.models import PreferenciaDashboard
from patrimonio.services.cuentas_por_cobrar import (
    fecha_evento_neat,
    primer_dia_mes,
    ventana_fuentes_cobro,
)
from patrimonio.services import revision_cobros
from patrimonio.services import preparacion_cobros
from patrimonio.services.proyeccion_cobros import resumen_mensual

DASHBOARD_WIDGETS = (
    "patrimonio", "activos", "pasivos", "liquidez_resumen", "cupos",
    "flujo", "liquidez_grafico", "patrimonio_historico", "tendencia",
    "propiedades", "top_activos",
)


def ensure_pasivos_for_products(user):
    """
    Ensures that all relevant financial products (TDC, CREDITO_CONSUMO, CREDITO_HIPOTECARIO) 
    have a corresponding Pasivo record.
    """
    from configuracion.models import Producto
    from patrimonio.models import Pasivo
    
    productos_deuda = Producto.objects.filter(
        user=user,
        tipo__in=['CREDITO_HIPOTECARIO', 'CREDITO_CONSUMO', 'TDC'],
        activo=True
    )
    
    for prod in productos_deuda:
        if not Pasivo.objects.filter(producto=prod).exists():
            Pasivo.objects.create(
                user=user,
                producto=prod,
                nombre=f"{prod.banco.nombre} - {prod.nombre}",
                tipo=prod.tipo,
                monto_clp=0,
                monto_usd=0,
                activo=True,
                notas=f"Auto-generado desde Entidad Financiera: {prod.banco.nombre} - {prod.nombre}"
            )

@login_required
def dashboard(request):
    """Dashboard with key metrics and charts"""
    user = request.user

    patrimonio_actual = resumen_patrimonio(user)
    tipo_cambio = patrimonio_actual["tipo_cambio"]
    activos_all = patrimonio_actual["activos"]
    pasivos = patrimonio_actual["pasivos"]
    for activo in activos_all:
        valor = patrimonio_actual["valores_activos"][activo.pk]
        activo.monto_clp, activo.monto_usd = valor.clp, valor.usd
    total_activos = patrimonio_actual["activos_total_clp"]
    total_pasivos = patrimonio_actual["pasivos_total_clp"]
    patrimonio_neto = patrimonio_actual["patrimonio_neto_clp"]
    total_liquidos = patrimonio_actual["activos_liquidos_clp"]
    total_no_liquidos = patrimonio_actual["activos_no_liquidos_clp"]
    liquidez_pct = patrimonio_actual["liquidez_pct"]

    # El panel comparte la caja facturada del consolidado mensual.
    selected_year = request.GET.get('year')
    selected_month = request.GET.get('month')
    ultimo_mes = date.today().replace(day=1) - timedelta(days=1)
    try:
        periodo_solicitado = date(int(selected_year), int(selected_month), 1)
        target_year, target_month = periodo_solicitado.year, periodo_solicitado.month
    except (TypeError, ValueError):
        target_year, target_month = ultimo_mes.year, ultimo_mes.month
    periodo_dashboard = date(target_year, target_month, 1)
    flujo = resumen_gastos(user, periodo_dashboard, periodo_dashboard, "caja")
    ingresos = flujo["totales"]["ingresos_clp"]
    total_gastos = flujo["totales"]["egresos_clp"]
    salidas = [item for grupo in flujo['categorias'] for item in grupo['movimientos'] if item['direccion'] == 'EGRESO']
    # Each contribution belongs to exactly one summary segment.
    gastos_fijos = sum(((item['monto_calculo_clp'] or Decimal('0')) for item in salidas
                       if item['esencialidad'] == 'FIJO' and item['categoria'] != 'Suscripciones'), Decimal('0'))
    suscripciones = sum(((item['monto_calculo_clp'] or Decimal('0')) for item in salidas
                         if item['categoria'] == 'Suscripciones'), Decimal('0'))
    tarjetas = sum(
        ((item["monto_calculo_clp"] or Decimal('0')) for grupo in flujo["categorias"] for item in grupo["movimientos"]
         if item["direccion"] == "EGRESO" and item["fuente"] == "TDC"),
        Decimal("0"),
    )
    otros = total_gastos - gastos_fijos - suscripciones
    
    # Historical Patrimonio (snapshots)
    snapshots = SnapshotPatrimonio.objects.filter(user=user).order_by('fecha')
    snapshot_labels = [s.fecha.strftime('%b %Y') for s in snapshots]
    snapshot_activos = [float(s.activos_total_clp) for s in snapshots]
    snapshot_pasivos = [float(s.pasivos_total_clp) for s in snapshots]
    snapshot_neto = [float(s.patrimonio_neto_clp) for s in snapshots]
    
    # Histórico de caja confirmada, consistente con Gastos Mensuales.
    today = date.today()
    historico_meses = []
    def restar_meses(base, cantidad):
        total = base.year * 12 + base.month - 1 - cantidad
        return date(total // 12, total % 12 + 1, 1)

    for i in range(5, -1, -1):
        d = restar_meses(today.replace(day=1), i)
        y, m = d.year, d.month
        mes_flujo = resumen_gastos(user, date(y, m, 1), date(y, m, 1), "caja")
        ing = mes_flujo["totales"]["ingresos_clp"]
        gas = mes_flujo["totales"]["egresos_clp"]
        historico_meses.append({
            'label': f"{m:02d}/{y}",
            'ingresos': float(ing),
            'gastos': float(gas),
            'balance': float(ing - gas),
        })
    
    # Get departments and top activos for previews
    departamentos = Departamento.objects.filter(user=user)[:3]
    top_activos = sorted(activos_all, key=lambda activo: activo.monto_clp, reverse=True)[:4]

    resumen_cupos = cupos_tdc(user)
    
    available_months = [
        {"year": restar_meses(today.replace(day=1), i).year,
         "mes": restar_meses(today.replace(day=1), i).month}
        for i in range(24)
    ]
    
    context = {
        'widget_order': (
            PreferenciaDashboard.objects.filter(user=user)
            .values_list('orden_widgets', flat=True).first() or list(DASHBOARD_WIDGETS)
        ),
        'patrimonio_neto': patrimonio_neto,
        'total_activos': total_activos,
        'total_pasivos': total_pasivos,
        'total_liquidos': total_liquidos,
        'total_no_liquidos': total_no_liquidos,
        'liquidez_pct': liquidez_pct,
        'ingresos_mes': ingresos,
        'gastos_mes': total_gastos,
        'balance_mes': ingresos - total_gastos,
        'gastos_fijos': gastos_fijos,
        'suscripciones': suscripciones,
        'tarjetas': tarjetas,
        'otros': otros,
        'tipo_cambio': tipo_cambio,
        'departamentos': departamentos,
        'top_activos': top_activos,
        'target_year': target_year,
        'target_month': target_month,
        'snapshot_labels': snapshot_labels,
        'snapshot_activos': snapshot_activos,
        'snapshot_pasivos': snapshot_pasivos,
        'snapshot_neto': snapshot_neto,
        'historico_meses': historico_meses,
        'available_months': available_months,
        'cupos_tdc': resumen_cupos,
        'cobertura_estados': flujo['cobertura'],
        'flujo_sin_conversion': flujo.get('sin_conversion', 0),
        'filas_por_revisar_moneda': patrimonio_actual["filas_por_revisar_moneda"],
        'falta_tasa_usd': patrimonio_actual["falta_tasa_usd"],
        'falta_tasa_uf': patrimonio_actual["falta_tasa_uf"],
    }
    
    return render(request, 'dashboard.html', context)


@login_required
@require_http_methods(["POST"])
def guardar_dashboard_widgets(request):
    try:
        payload = json.loads(request.body)
        orden = payload.get("orden") if isinstance(payload, dict) else None
    except (ValueError, TypeError):
        return JsonResponse({"error": "Formato inválido"}, status=400)
    if (not isinstance(orden, list) or not orden or len(orden) > len(DASHBOARD_WIDGETS)
            or any(not isinstance(item, str) or item not in DASHBOARD_WIDGETS for item in orden)
            or len(orden) != len(set(orden))):
        return JsonResponse({"error": "Orden inválido"}, status=400)
    PreferenciaDashboard.objects.update_or_create(
        user=request.user, defaults={"orden_widgets": orden},
    )
    return JsonResponse({"ok": True})


@login_required
def gastos_table(request):
    """Consolidado mensual de entradas y obligaciones ya facturadas."""
    from banca.services.presupuesto import proyeccion_no_facturados
    user = request.user
    today = date.today()
    last_month_date = today.replace(day=1) - timedelta(days=1)
    try:
        selected_year = int(request.GET.get("year", last_month_date.year))
        selected_month = int(request.GET.get("month", last_month_date.month))
        date(selected_year, selected_month, 1)
    except (TypeError, ValueError):
        selected_year, selected_month = last_month_date.year, last_month_date.month
    years = range(today.year - 2, today.year + 2)
    categorias = CategoriaTematica.objects.filter(user=user, activa=True)
    productos = Producto.objects.filter(user=user, activo=True, reemplazado_por__isnull=True).select_related("banco")
    gastos_programados = GastoProgramado.objects.filter(user=user, activo=True)
    periodos_movimientos = []
    vistos = set()
    for fecha_movimiento in MovimientoBancario.objects.filter(
        user=user,
        por_revisar=False,
    ).exclude(estado="DESCARTADO_CIERRE").order_by("-fecha").values_list("fecha", flat=True):
        clave = (fecha_movimiento.year, fecha_movimiento.month)
        if clave not in vistos:
            vistos.add(clave)
            periodos_movimientos.append({"year": fecha_movimiento.year, "month": fecha_movimiento.month})
    total_ahorro_programado = sum(g.ahorro_mensual for g in gastos_programados)
    context = {
        'current_year': selected_year,
        'current_month': selected_month,
        'initial_perspective': 'caja',
        'default_year': last_month_date.year,
        'default_month': last_month_date.month,
        'years': years,
        'categorias': categorias,
        'productos': productos,
        'bancos': Banco.objects.filter(productos__user=user).distinct(),
        'departamentos_manual': Departamento.objects.filter(user=user, vinculos_externos__isnull=True, activo=True),
        'gastos_programados': gastos_programados,
        'periodos_movimientos': periodos_movimientos[:12],
        'proyeccion': proyeccion_no_facturados(user),
        'total_ahorro_anual': total_ahorro_programado,
        'total_ahorro_trimestral': Decimal('0'),
    }
    
    return render(request, 'gastos_consolidado.html', context)


@login_required
def patrimonio(request):
    """Patrimony/Net worth view - Summary page"""
    user = request.user
    
    resumen = resumen_patrimonio(user)
    activos_all, pasivos = resumen["activos"], resumen["pasivos"]
    tipo_cambio = resumen["tipo_cambio"]
    for activo in activos_all:
        valor = resumen["valores_activos"][activo.pk]
        activo.monto_clp, activo.monto_usd = valor.clp, valor.usd
    total_activos = resumen["activos_total_clp"]
    total_pasivos = resumen["pasivos_total_clp"]
    patrimonio_neto = resumen["patrimonio_neto_clp"]
    total_liquidos = resumen["activos_liquidos_clp"]
    total_no_liquidos = resumen["activos_no_liquidos_clp"]
    liquidez_pct = resumen["liquidez_pct"]
    departamentos = resumen["departamentos"]
    
    snapshots = SnapshotPatrimonio.objects.filter(user=user).order_by('-fecha')[:12]
    
    context = {
        'total_activos': total_activos,
        'total_pasivos': total_pasivos,
        'patrimonio_neto': patrimonio_neto,
        'total_liquidos': total_liquidos,
        'total_no_liquidos': total_no_liquidos,
        'liquidez_pct': liquidez_pct,
        'activos_count': len(activos_all) + len(departamentos),
        'pasivos_count': len(pasivos),
        'snapshots': snapshots,
        'tipo_cambio': tipo_cambio,
        'filas_por_revisar_moneda': resumen["filas_por_revisar_moneda"],
        'falta_tasa_usd': resumen["falta_tasa_usd"],
        'falta_tasa_uf': resumen["falta_tasa_uf"],
    }
    
    return render(request, 'patrimonio.html', context)


@login_required
def activos_view(request):
    """Unified Assets view (replaces Inversiones)"""
    user = request.user
    
    resumen = resumen_patrimonio(user)
    tipo_cambio = resumen["tipo_cambio"]
    valor_uf = tipo_cambio.uf if tipo_cambio else Decimal("0")
    
    # All user assets (excluding departamentos which are auto-generated)
    activos_qs = resumen["activos"]
    origenes = origenes_activos(user, activos_qs)
    for activo in activos_qs:
        activo.origen_sincronizacion = origenes[activo.pk]
        valor = resumen["valores_activos"][activo.pk]
        activo.monto_clp, activo.monto_usd = valor.clp, valor.usd
    
    # Build departamento assets
    departamentos = Departamento.objects.filter(user=user)
    depto_activos = []
    total_depto_clp = Decimal(0)
    for d in departamentos:
        monto = d.valor_actual_uf * valor_uf
        total_depto_clp += monto
        depto_activos.append({
            'id': None,
            'nombre': f"Depto {d.codigo}",
            'tipo': 'DEPARTAMENTO',
            'tipo_display': 'Departamento',
            'horizonte_temporal': 'LARGO_PLAZO',
            'horizonte_display': 'Largo Plazo',
            'es_liquido': False,
            'monto_clp': monto,
            'monto_usd': Decimal(0),
            'updated_at': d.updated_at,
            'es_depto': True,
            'depto_id': d.id,
        })
    
    total_invertido = resumen["activos_total_clp"]
    
    # Group by horizonte_temporal
    HORIZONTE_ORDER = {'EFECTIVO': 0, 'CORTO_PLAZO': 1, 'MEDIANO_PLAZO': 2, 'LARGO_PLAZO': 3}
    HORIZONTE_LABELS = dict(Activo.HORIZONTE_CHOICES)
    
    grouped = {}
    for h_key, h_label in Activo.HORIZONTE_CHOICES:
        items = [a for a in activos_qs if a.horizonte_temporal == h_key]
        # Add depto assets to LARGO_PLAZO
        if h_key == 'LARGO_PLAZO':
            grouped[h_key] = {
                'label': h_label,
                'items': items,
                'depto_items': depto_activos,
                'total': sum(a.monto_clp for a in items) + total_depto_clp,
            }
        else:
            grouped[h_key] = {
                'label': h_label,
                'items': items,
                'depto_items': [],
                'total': sum(a.monto_clp for a in items),
            }
    
    # Calculate portfolio percentages for chart
    chart_data = []
    for a in activos_qs:
        chart_data.append({'nombre': a.nombre, 'monto': float(a.monto_clp)})
    for d in depto_activos:
        chart_data.append({'nombre': d['nombre'], 'monto': float(d['monto_clp'])})
    
    # Liquidity totals
    total_liquidos = resumen["activos_liquidos_clp"]
    total_no_liquidos = resumen["activos_no_liquidos_clp"]
    liquidez_pct = resumen["liquidez_pct"]
    
    # Portfolio history (from HistorialActivo)
    from django.db.models.functions import TruncMonth
    historico_qs = HistorialActivo.objects.filter(
        activo__user=user,
        activo__activo=True,
        fecha__gte=date.today() - timedelta(days=365)
    ).annotate(month=TruncMonth('fecha')).values('month').annotate(total=Sum('monto_clp')).order_by('month')
    
    historico = {
        'labels': [h['month'].strftime('%b %Y') for h in historico_qs],
        'values': [float(h['total']) for h in historico_qs]
    }
    
    context = {
        'grouped': grouped,
        'total_invertido': total_invertido,
        'total_liquidos': total_liquidos,
        'total_no_liquidos': total_no_liquidos,
        'liquidez_pct': liquidez_pct,
        'activos_count': len(activos_qs) + len(depto_activos),
        'chart_data': chart_data,
        'historico': historico if historico['labels'] else None,
        'tipo_cambio': tipo_cambio,
        'filas_por_revisar_moneda': resumen["filas_por_revisar_moneda"],
        'filas_por_revisar_fuente': sum(a.requiere_revision_fuente for a in activos_qs),
        'falta_tasa_usd': resumen["falta_tasa_usd"],
        'falta_tasa_uf': resumen["falta_tasa_uf"],
    }
    
    return render(request, 'activos.html', context)


def _cobro_periodo(valor, defecto=None):
    try:
        return datetime.strptime(valor, "%Y-%m").date().replace(day=1)
    except (TypeError, ValueError):
        return defecto


def _cobro_fecha(valor, defecto):
    try:
        return date.fromisoformat(valor)
    except (TypeError, ValueError):
        return defecto


def _cobro_decimal(valor):
    try:
        texto = str(valor or "").strip()
        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        monto = Decimal(texto)
        return monto if monto.is_finite() and monto > 0 else None
    except (InvalidOperation, ValueError):
        return None


def _fuentes_cobro_disponibles(user, periodo=None):
    """Fuentes del mes visible que se pueden asociar sin cruzar usuarios."""
    if periodo:
        periodo = primer_dia_mes(periodo)
        inicio, fin = ventana_fuentes_cobro(periodo)
    fuentes = []
    movimientos_query = MovimientoBancario.objects.filter(
        user=user,
        producto__tipo="TDC",
        monto__gt=0,
    )
    if periodo:
        movimientos_query = movimientos_query.filter(fecha__range=(inicio, fin))
    movimientos = (
        movimientos_query
        .exclude(estado="DESCARTADO_CIERRE")
        .select_related("producto", "producto__banco")
        .order_by("-fecha", "-id")[:150]
    )
    for movimiento in movimientos:
        monto = movimiento.monto or movimiento.monto_clp
        monto_label = f"US${monto:,.2f}" if movimiento.moneda == "USD" else f"${monto:,.0f}"
        fuentes.append(
            {
                "value": f"movimiento:{movimiento.id}",
                "label": (
                    f"Tarjeta · {movimiento.fecha:%d/%m/%Y} · "
                    f"{movimiento.descripcion} · {monto_label}"
                ),
                "monto": abs(monto),
                "fecha": movimiento.fecha,
                "descripcion": movimiento.descripcion,
                "moneda": movimiento.moneda,
            }
        )

    eventos_query = EventoFinancieroExterno.objects.filter(
        user=user,
        fuente="neat",
        monto_neto__gt=0,
    )
    if periodo:
        eventos_query = eventos_query.filter(
            Q(fecha_cargo__range=(inicio, fin))
            | Q(fecha_evento__range=(inicio, fin))
            | Q(periodo=periodo)
            | Q(fecha_cargo__isnull=True, fecha_evento__isnull=True, periodo__isnull=True)
        )
    eventos = list(
        eventos_query
        .exclude(estado="FALLIDO")
        .select_related("pago_neat", "importacion")
        .order_by("-fecha_cargo", "-fecha_evento", "-periodo", "-id")[:300]
    )
    grupos_eventos = {}
    ocurrencias_por_importacion = {}
    for evento in eventos:
        fecha = fecha_evento_neat(evento)
        monto = evento.monto_neto or evento.monto_bruto
        huella = huella_pago_neat(evento.metadata or {})
        if huella and evento.importacion_id:
            identidad = (huella, evento.moneda, fecha, monto)
            clave_ocurrencia = (evento.importacion_id, *identidad)
            ocurrencia = ocurrencias_por_importacion.get(clave_ocurrencia, 0)
            ocurrencias_por_importacion[clave_ocurrencia] = ocurrencia + 1
            # La misma fila entre distintos archivos es una repetición de
            # reporte. Dos filas idénticas dentro de un solo CSV conservan
            # ordinales distintos y nunca se colapsan entre sí.
            clave = (*identidad, ocurrencia)
        else:
            clave = ("evento", evento.pk)
        grupos_eventos.setdefault(clave, []).append(evento)

    for grupo in grupos_eventos.values():
        evento = max(
            grupo,
            key=lambda item: (
                bool(getattr(item, "pago_neat", None)),
                bool(item.fecha_cargo or item.fecha_evento or item.periodo),
                bool(item.importacion_id),
                item.pk,
            ),
        )
        pago = getattr(evento, "pago_neat", None)
        metadata = evento.metadata or {}
        descripcion = (
            (pago.destinatario if pago else "")
            or metadata.get("nombre_destinatario")
            or metadata.get("destinatario")
            or metadata.get("beneficiario")
            or (pago.cuenta if pago else "")
            or metadata.get("cuenta")
            or "NeatPagos"
        )
        monto = evento.monto_neto or evento.monto_bruto
        fecha = fecha_evento_neat(evento)
        if not fecha:
            # No asociamos un evento sin fecha: usar hoy lo haría parecer una
            # transacción actual y podría vincularlo al mes equivocado.
            continue
        if periodo and not (inicio <= fecha <= fin):
            continue
        alias = str(metadata.get("alias") or "").strip()
        if alias and alias.casefold() not in descripcion.casefold():
            descripcion = f"{descripcion} · {alias}"
        monto_label = f"US${monto:,.2f}" if evento.moneda == "USD" else f"${monto:,.0f}"
        etiqueta_duplicado = ""
        importaciones = {item.importacion_id for item in grupo if item.importacion_id}
        if len(importaciones) > 1:
            etiqueta_duplicado = (
                f" · Fila coincidente en {len(importaciones)} importaciones "
                "(dia_de_pago, cuenta y monto)"
            )
        fuentes.append(
            {
                "value": f"neat:{evento.id}",
                "label": f"NeatPagos · {fecha:%d/%m/%Y} · {descripcion} · {monto_label}{etiqueta_duplicado}",
                "monto": abs(monto),
                "fecha": fecha,
                "descripcion": descripcion,
                "moneda": evento.moneda,
            }
        )
    return fuentes


def _fuente_cobro_desde_seleccion(seleccion, user):
    """Resuelve una fuente enviada por un formulario de cuentas por cobrar."""
    seleccion = (seleccion or "").strip()
    if seleccion.startswith("movimiento:"):
        return get_object_or_404(
            MovimientoBancario,
            pk=seleccion.removeprefix("movimiento:"),
            user=user,
            producto__tipo="TDC",
        )
    if seleccion.startswith("neat:"):
        return get_object_or_404(
            EventoFinancieroExterno,
            pk=seleccion.removeprefix("neat:"),
            user=user,
            fuente="neat",
        )
    return None


@login_required
def cuentas_por_cobrar_view(request):
    """Panel de cuentas por cobrar y texto listo para compartir."""
    hoy = timezone.localdate()
    periodo_cobro = _cobro_periodo(
        request.GET.get("periodo"), primer_dia_mes(hoy)
    )
    propuesta_cuenta_id = str(request.GET.get("propuesta", "")).strip()
    detalle_qs = (
        DetalleCuentaPorCobrar.objects.select_related(
            "movimiento", "evento_externo"
        ).prefetch_related("abonos")
    )
    cuentas = list(
        CuentaPorCobrar.objects.filter(user=request.user)
        .select_related("activo_patrimonial")
        .prefetch_related(
            Prefetch("detalles", queryset=detalle_qs),
            "abonos",
            "calculos",
        )
    )
    fuentes_cobro = _fuentes_cobro_disponibles(request.user, periodo_cobro)
    fuentes_por_valor = {fuente["value"]: fuente for fuente in fuentes_cobro}

    for cuenta in cuentas:
        cuenta.resumen_mensual = resumen_mensual(cuenta, periodo_cobro)
        calculos = list(cuenta.calculos.all())
        etiquetas = dict(PartidaRevisionCobro.DECISIONES)
        etiquetas["REFERENCIA"] = "Referencia sin validar"
        for calculo in calculos:
            calculo.entradas_visibles = []
            for entrada in calculo.desglose.get("entradas", []):
                visible = dict(entrada)
                visible["fecha"] = date.fromisoformat(entrada["fecha"])
                visible["monto"] = Decimal(entrada["monto"])
                visible["efecto"] = Decimal(entrada.get("efecto", "0"))
                visible["decision_label"] = etiquetas.get(entrada.get("decision"), "Referencia sin validar")
                calculo.entradas_visibles.append(visible)
        cuenta.ultimo_confirmado = next((c for c in calculos if c.estado == "CONFIRMADO"), None)
        periodo_preparacion = max(periodo_cobro, cuenta.ultimo_confirmado.periodo) if cuenta.ultimo_confirmado else periodo_cobro
        cuenta.url_preparacion_mensual = (reverse("renovar_cuenta_por_cobrar", args=[cuenta.pk]) +
                                         f"?periodo={periodo_preparacion:%Y-%m}" +
                                         ("&referencia_id=sin_saldo" if not cuenta.ultimo_confirmado else ""))
        cuenta.confirmado_mes = next((c for c in calculos if c.estado == "CONFIRMADO" and c.periodo == periodo_cobro), None)
        cuenta.borrador = next((c for c in calculos if c.estado == "BORRADOR" and c.periodo == periodo_cobro), None)
        cuenta.referencias = [c for c in calculos if c.estado == "REFERENCIA"]
        cuenta.historial = [c for c in calculos if c.estado != "BORRADOR"]
        referencia_usuario = next((c for c in cuenta.referencias if c.referencia_usuario), None)
        cuenta.referencia_aportada = referencia_usuario
        cuenta.base_revision = cuenta.borrador.anterior if cuenta.borrador else cuenta.ultimo_confirmado or revision_cobros.calculo_anterior(cuenta, periodo_cobro)
        cuenta.evidencia_base = preparacion_cobros.evidencia_calculo(cuenta, cuenta.base_revision)
        cuenta.patrones_revision = cuenta.borrador.desglose.get("preparacion", {}).get("patrones", []) if cuenta.borrador else []
        cuenta.calculo_para_copiar = cuenta.confirmado_mes or cuenta.ultimo_confirmado or revision_cobros.calculo_anterior(cuenta, periodo_cobro)
        cuenta.texto_para_compartir = cuenta.resumen_mensual.texto
        partidas_revision = list(cuenta.partidas_revision.filter(resuelta_en__isnull=True)
                                 .select_related("conciliar_con", "movimiento", "evento_externo", "detalle"))
        cuenta.partidas_pendientes = [p for p in partidas_revision if p.periodo_vista <= periodo_cobro]
        ediciones_pendientes = {}
        for partida in partidas_revision:
            if partida.tipo != "EDITAR" or not partida.detalle_id:
                continue
            partida.periodo_revision = revision_cobros.periodo_correccion(
                cuenta, partida.periodo, reconstruir=partida.registrada_por_usuario,
            )
            borrador_edicion = next((c for c in calculos if c.estado == "BORRADOR" and c.periodo == partida.periodo_revision), None)
            base_edicion = borrador_edicion.anterior if borrador_edicion else revision_cobros.calculo_anterior(cuenta, partida.periodo_revision)
            contabilizados_edicion = base_edicion.desglose.get("contabilizados", {}) if base_edicion else {}
            partida.efecto_propuesto = partida.monto - Decimal(contabilizados_edicion.get(
                f"detalle:{partida.detalle_id}", str(partida.detalle.monto),
            ))
            if borrador_edicion:
                partida.url_revision = (reverse("cuentas_por_cobrar") +
                                        f"?gestionar={cuenta.pk}&periodo={partida.periodo_revision:%Y-%m}#partida-{partida.pk}")
            else:
                partida.url_revision = (reverse("renovar_cuenta_por_cobrar", args=[cuenta.pk]) +
                                        f"?periodo={partida.periodo_revision:%Y-%m}" +
                                        ("&referencia_id=sin_saldo" if partida.registrada_por_usuario and not cuenta.ultimo_confirmado else ""))
            ediciones_pendientes[partida.detalle_id] = partida
        cuenta.faltantes_revision = preparacion_cobros.resumen_preparacion(cuenta, periodo_cobro, cuenta.base_revision)["faltantes"] if cuenta.borrador else []
        incluidos = cuenta.base_revision.desglose.get("contabilizados", {}) if cuenta.base_revision else {}
        inicio_anticipacion, _ = ventana_fuentes_cobro(periodo_cobro)
        for partida in cuenta.partidas_pendientes:
            claves = {partida.clave, f"movimiento:{partida.movimiento_id}", f"neat:{partida.evento_externo_id}"}
            partida.ya_contabilizada = partida.tipo in {"CARGO", "ABONO"} and bool(claves & incluidos.keys())
            partida.pago_adelantado_neat = (partida.tipo == "CARGO" and bool(partida.evento_externo_id)
                                           and inicio_anticipacion <= partida.fecha < periodo_cobro)
            partida.cargos_respaldados = preparacion_cobros.respaldos_cargo(cuenta, partida.movimiento or partida.evento_externo) if partida.tipo == "CARGO" and (partida.movimiento_id or partida.evento_externo_id) else []
        cuenta.abonos_conciliables = [a for a in cuenta.abonos.all() if a.vigente]
        cuenta.gestionar = str(request.GET.get("gestionar", "")) == str(cuenta.pk) or propuesta_cuenta_id == str(cuenta.pk)
        cuenta.partida_edicion = next((p for p in partidas_revision
            if str(p.pk) == request.GET.get("editar_partida") and p.registrada_por_usuario
            and p.tipo in {"CARGO", "ABONO"} and not p.movimiento_id and not p.evento_externo_id
            and not p.detalle_id and not p.abono_id), None)
        if cuenta.partida_edicion:
            cuenta.gestionar = True
        todos_los_detalles = list(cuenta.detalles.all())
        for detalle in todos_los_detalles:
            pendiente = ediciones_pendientes.get(detalle.pk)
            detalle.edicion_pendiente = pendiente if pendiente and pendiente.periodo_vista == periodo_cobro else None
        cuenta.abonos_mes = [a for a in cuenta.abonos.all() if (a.periodo or a.fecha.replace(day=1)) == periodo_cobro]
        cuenta.detalles_visibles = [
            detalle for detalle in todos_los_detalles if detalle.periodo == periodo_cobro and detalle.vigente
        ]
        cuenta.detalles_abono = [
            detalle for detalle in todos_los_detalles if detalle.saldo_pendiente > 0 and detalle.vigente and detalle.periodo <= periodo_cobro
        ]
        detalle_edicion_id = str(request.GET.get("editar", "")).strip()
        if detalle_edicion_id and any(
            str(detalle.id) == detalle_edicion_id for detalle in todos_los_detalles
        ):
            cuenta.gestionar = True
            cuenta.detalle_edicion = next(
                detalle for detalle in todos_los_detalles if str(detalle.id) == detalle_edicion_id
            )
            edicion_guardada = ediciones_pendientes.get(cuenta.detalle_edicion.pk)
            cuenta.valores_edicion = edicion_guardada or cuenta.detalle_edicion
            cuenta.periodo_edicion = (edicion_guardada.periodo_vista
                                     if edicion_guardada
                                     else cuenta.detalle_edicion.periodo)
            if cuenta.valores_edicion.movimiento_id:
                fuente_valor = f"movimiento:{cuenta.valores_edicion.movimiento_id}"
            elif cuenta.valores_edicion.evento_externo_id:
                fuente_valor = f"neat:{cuenta.valores_edicion.evento_externo_id}"
            else:
                fuente_valor = ""
            fuente_actual = fuentes_por_valor.get(fuente_valor)
            cuenta.detalle_edicion.fuente_actual_valor = fuente_valor
            cuenta.detalle_edicion.fuente_actual_label = (
                fuente_actual["label"] if fuente_actual else "Fuente actualmente asociada"
            )
    cuentas.sort(key=lambda cuenta: (not cuenta.activo, -cuenta.saldo_pendiente, cuenta.nombre.casefold()))
    resumen_clp = sum(
        (cuenta.resumen_mensual.saldo_final for cuenta in cuentas if cuenta.moneda == "CLP"),
        Decimal("0"),
    )
    resumen_usd = sum(
        (cuenta.resumen_mensual.saldo_final for cuenta in cuentas if cuenta.moneda == "USD"),
        Decimal("0"),
    )
    context = {
        "cuentas_por_cobrar": cuentas,
        "fuentes_cobro": fuentes_cobro,
        "periodo_actual_cobro": periodo_cobro.strftime("%Y-%m"),
        "periodo_cobro": periodo_cobro,
        "periodo_anterior_cobro": (periodo_cobro - timedelta(days=1)).strftime("%Y-%m"),
        "periodo_siguiente_cobro": (periodo_cobro.replace(day=28) + timedelta(days=4)).replace(day=1).strftime("%Y-%m"),
        "fecha_actual_cobro": (hoy if hoy.replace(day=1) == periodo_cobro else periodo_cobro).isoformat(),
        "resumen_cobro_clp": resumen_clp,
        "resumen_cobro_usd": resumen_usd,
    }
    return render(request, "cuentas_por_cobrar.html", context)


def _volver_cuenta_cobro(cuenta, periodo=None, editar=None, *, editar_partida=None):
    url = reverse("cuentas_por_cobrar")
    parametros = [f"gestionar={cuenta.pk}"]
    if periodo:
        parametros.append(f"periodo={periodo:%Y-%m}")
    if editar:
        parametros.append(f"editar={editar}")
    if editar_partida:
        parametros.append(f"editar_partida={editar_partida}")
    if parametros:
        url += "?" + "&".join(parametros)
    return redirect(f"{url}#cuenta-{cuenta.pk}")


@require_http_methods(["POST"])
@login_required
def crear_cuenta_por_cobrar(request):
    nombre = request.POST.get("nombre", "").strip()
    moneda = request.POST.get("moneda", "CLP")
    if not nombre:
        messages.error(request, "Indica un nombre para la cuenta por cobrar.")
        return redirect(reverse("cuentas_por_cobrar") + "?crear=1")
    if moneda not in dict(CuentaPorCobrar.MONEDA_CHOICES):
        messages.error(request, "La moneda seleccionada no es válida.")
        return redirect(reverse("cuentas_por_cobrar") + "?crear=1")
    cuenta = CuentaPorCobrar.objects.create(
        user=request.user,
        nombre=nombre,
        descripcion=request.POST.get("descripcion", "").strip(),
        moneda=moneda,
    )
    messages.success(request, f"Cuenta por cobrar «{cuenta.nombre}» creada y agregada a Activos.")
    return _volver_cuenta_cobro(cuenta)


@require_http_methods(["POST"])
@login_required
def agregar_detalle_cuenta_por_cobrar(request):
    cuenta = get_object_or_404(CuentaPorCobrar, pk=request.POST.get("cuenta_id"), user=request.user)
    if not cuenta.activo:
        messages.error(request, "La cuenta está archivada; reactívala antes de agregar movimientos.")
        return _volver_cuenta_cobro(cuenta)

    from patrimonio.services.cuentas_por_cobrar import informacion_fuente

    seleccion = request.POST.get("fuente", "").strip()
    fuente = _fuente_cobro_desde_seleccion(seleccion, request.user)

    if fuente is not None:
        info = informacion_fuente(fuente)
        if info["moneda"] != cuenta.moneda:
            messages.error(request, "La moneda de la transacción no coincide con la cuenta.")
            return _volver_cuenta_cobro(cuenta)
        if (
            (
                isinstance(fuente, MovimientoBancario)
                and DetalleCuentaPorCobrar.objects.filter(movimiento=fuente).exists()
            )
            or (
                isinstance(fuente, EventoFinancieroExterno)
                and DetalleCuentaPorCobrar.objects.filter(evento_externo=fuente).exists()
            )
        ):
            messages.info(request, "Esa transacción ya está asociada a una cuenta por cobrar.")
            return _volver_cuenta_cobro(cuenta)
        monto = info["monto"]
        fecha = info["fecha"] or timezone.localdate()
        descripcion = request.POST.get("descripcion", "").strip() or info["descripcion"]
    else:
        monto = _cobro_decimal(request.POST.get("monto"))
        fecha = _cobro_fecha(request.POST.get("fecha"), timezone.localdate())
        descripcion = request.POST.get("descripcion", "").strip()

    if not monto or not descripcion:
        messages.error(request, "Para agregar una línea necesitas descripción y un monto mayor que cero.")
        return _volver_cuenta_cobro(cuenta)
    periodo = _cobro_periodo(request.POST.get("periodo"), fecha.replace(day=1))
    try:
        partida = revision_cobros.proponer_partida(
            cuenta, "CARGO", periodo=periodo, fuente=fuente, descripcion=descripcion,
            fecha=fecha, monto=monto, provisional=fuente is None and request.POST.get("provisional") == "1",
            registrada_por_usuario=True,
        )
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return _volver_cuenta_cobro(cuenta, periodo)
    messages.success(request, f"«{partida.descripcion}» pendiente de revisar. Prepara el cálculo y confirma su aplicación.")
    return _volver_cuenta_cobro(cuenta, periodo)


@require_http_methods(["POST"])
@login_required
def editar_detalle_cuenta_por_cobrar(request, pk):
    detalle = get_object_or_404(
        DetalleCuentaPorCobrar.objects.select_related("cuenta"),
        pk=pk,
        cuenta__user=request.user,
    )
    cuenta = detalle.cuenta
    if not cuenta.activo:
        messages.error(request, "La cuenta está archivada; reactívala antes de editar líneas.")
        return _volver_cuenta_cobro(cuenta, detalle.periodo)

    from patrimonio.services.cuentas_por_cobrar import informacion_fuente

    fuente = _fuente_cobro_desde_seleccion(request.POST.get("fuente"), request.user)
    if fuente is not None:
        info = informacion_fuente(fuente)
        if info["moneda"] != cuenta.moneda:
            messages.error(request, "La moneda de la transacción no coincide con la cuenta.")
            return _volver_cuenta_cobro(cuenta, detalle.periodo, detalle.pk)
        if isinstance(fuente, MovimientoBancario):
            existente = DetalleCuentaPorCobrar.objects.filter(movimiento=fuente).exclude(pk=detalle.pk).first()
        else:
            existente = DetalleCuentaPorCobrar.objects.filter(evento_externo=fuente).exclude(pk=detalle.pk).first()
        if existente:
            messages.info(
                request,
                f"No se cambió la línea: esa transacción ya está asociada a «{existente.descripcion}» "
                f"({existente.periodo:%m/%Y}). Elige una transacción de otro mes para no duplicarla.",
            )
            return _volver_cuenta_cobro(cuenta, detalle.periodo, detalle.pk)
        monto = info["monto"]
        fecha = info["fecha"] or detalle.fecha
        descripcion = request.POST.get("descripcion", "").strip() or detalle.descripcion
    else:
        monto = _cobro_decimal(request.POST.get("monto"))
        fecha = _cobro_fecha(request.POST.get("fecha"), detalle.fecha)
        descripcion = request.POST.get("descripcion", "").strip() or detalle.descripcion

    if not monto or not descripcion:
        messages.error(request, "Para editar una línea necesitas descripción y un monto mayor que cero.")
        return _volver_cuenta_cobro(cuenta, detalle.periodo, detalle.pk)

    periodo = _cobro_periodo(request.POST.get("periodo"), fecha.replace(day=1))
    periodo_anterior = detalle.periodo
    try:
        partida = revision_cobros.proponer_partida(
            cuenta, "EDITAR", periodo=periodo, detalle=detalle, fuente=fuente,
            descripcion=descripcion, fecha=fecha, monto=monto,
            provisional=fuente is None and request.POST.get("provisional") == "1",
            registrada_por_usuario=True,
        )
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return _volver_cuenta_cobro(cuenta, periodo_anterior, detalle.pk)
    messages.success(request, f"Vista mensual de {partida.periodo_vista:%m/%Y} actualizada. "
                             f"La confirmación patrimonial se revisará en {partida.periodo:%m/%Y}.")
    return _volver_cuenta_cobro(cuenta, partida.periodo_vista)


@require_http_methods(["POST"])
@login_required
@transaction.atomic
def editar_registro_pendiente_cobro(request, pk):
    partida = get_object_or_404(
        PartidaRevisionCobro, pk=pk, cuenta__user=request.user, resuelta_en__isnull=True,
        registrada_por_usuario=True, tipo__in=["CARGO", "ABONO"], movimiento__isnull=True,
        evento_externo__isnull=True, detalle__isnull=True, abono__isnull=True,
    )
    cuenta = CuentaPorCobrar.objects.select_for_update().get(pk=partida.cuenta_id)
    partida = get_object_or_404(PartidaRevisionCobro.objects.select_for_update(), pk=pk, resuelta_en__isnull=True)
    if not cuenta.activo:
        messages.error(request, "La cuenta está archivada; reactívala para editar registros.")
        return _volver_cuenta_cobro(cuenta, partida.periodo_vista)
    monto = _cobro_decimal(request.POST.get("monto"))
    if not monto:
        messages.error(request, "Ingresa un monto mayor que cero.")
        return _volver_cuenta_cobro(cuenta, partida.periodo_vista, editar_partida=partida.pk)
    periodo = _cobro_periodo(request.POST.get("periodo"), partida.periodo_vista)
    try:
        periodo_revision = revision_cobros.periodo_correccion(cuenta, periodo, reconstruir=True)
        revision_cobros.calculo_anterior(cuenta, periodo_revision)
        partida.monto, partida.periodo = monto, periodo_revision
        partida.periodo_mensual = periodo
        partida.fecha = _cobro_fecha(request.POST.get("fecha"), partida.fecha)
        partida.descripcion = request.POST.get("descripcion", "").strip() or partida.descripcion
        partida.decision, partida.conciliar_con = "PENDIENTE", None
        partida.save()
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return _volver_cuenta_cobro(cuenta, periodo, editar_partida=partida.pk)
    messages.success(request, "Registro actualizado. El total y el texto provisional del mes se recalcularon.")
    return _volver_cuenta_cobro(cuenta, periodo)


@require_http_methods(["POST"])
@login_required
def registrar_abono_cuenta_por_cobrar(request):
    cuenta = get_object_or_404(CuentaPorCobrar, pk=request.POST.get("cuenta_id"), user=request.user)
    monto = _cobro_decimal(request.POST.get("monto"))
    if not monto:
        messages.error(request, "El abono debe ser mayor que cero.")
        return _volver_cuenta_cobro(cuenta)
    detalle = None
    detalle_id = request.POST.get("detalle_id")
    if detalle_id:
        detalle = get_object_or_404(DetalleCuentaPorCobrar, pk=detalle_id, cuenta=cuenta)
    periodo = _cobro_periodo(request.POST.get("periodo"), primer_dia_mes(timezone.localdate()))
    try:
        revision_cobros.proponer_partida(
            cuenta, "ABONO", periodo=periodo, detalle=detalle,
            fecha=_cobro_fecha(request.POST.get("fecha"), timezone.localdate()), monto=monto,
            descripcion=request.POST.get("descripcion", "").strip(),
            registrada_por_usuario=True,
        )
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "Abono guardado en la vista mensual provisional. El saldo patrimonial se actualizará al confirmar.")
    return _volver_cuenta_cobro(cuenta, periodo)


@require_http_methods(["POST"])
@login_required
def borrar_abono_cuenta_por_cobrar(request, pk):
    abono = get_object_or_404(
        AbonoCuentaPorCobrar.objects.select_related("cuenta"),
        pk=pk,
        cuenta__user=request.user,
    )
    cuenta = abono.cuenta
    periodo = _cobro_periodo(request.POST.get("periodo"), primer_dia_mes(timezone.localdate()))
    try:
        revision_cobros.proponer_partida(cuenta, "RETIRAR_ABONO", periodo=periodo, abono=abono, registrada_por_usuario=True)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "Retiro propuesto. Confirma el ajuste para actualizar el saldo; el registro se conservará.")
    return _volver_cuenta_cobro(cuenta, periodo)


@require_http_methods(["POST"])
@login_required
def alternar_cuenta_por_cobrar(request, pk):
    cuenta = get_object_or_404(CuentaPorCobrar, pk=pk, user=request.user)
    cuenta.activo = not cuenta.activo
    cuenta.save(update_fields=["activo", "updated_at"])
    estado = "reactivada" if cuenta.activo else "archivada"
    messages.success(request, f"Cuenta «{cuenta.nombre}» {estado}.")
    return _volver_cuenta_cobro(cuenta)


@require_http_methods(["GET", "POST"])
@login_required
def renovar_cuenta_por_cobrar(request, pk):
    """Reúne las fuentes asignadas y ofrece documentos para completar la revisión."""
    cuenta = get_object_or_404(CuentaPorCobrar, pk=pk, user=request.user)
    datos = request.POST if request.method == "POST" else request.GET
    periodo = _cobro_periodo(datos.get("periodo"), primer_dia_mes(timezone.localdate()))
    if not cuenta.activo:
        messages.error(request, "Reactiva la cuenta antes de preparar una propuesta.")
        return _volver_cuenta_cobro(cuenta, periodo)
    try:
        anterior = revision_cobros.calculo_anterior(cuenta, periodo, datos.get("referencia_id"))
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return _volver_cuenta_cobro(cuenta, periodo)
    if request.method == "POST" and request.POST.get("accion") == "importar":
        from banca.services.estados_cuenta import importar_estado_cuenta
        from banca.services.fuentes_externas import importar_archivo_externo
        archivos = [("banco", f) for f in request.FILES.getlist("estados")]
        archivos += [("neat", f) for f in request.FILES.getlist("neat")]
        if not archivos:
            messages.error(request, "Selecciona un estado de cuenta, una cartola o un reporte NeatPagos.")
        for fuente, archivo in archivos:
            try:
                if archivo.size > 15 * 1024 * 1024:
                    raise ValueError("Supera el límite de 15 MB.")
                if fuente == "banco":
                    if not archivo.name.lower().endswith(".pdf"):
                        raise ValueError("Los estados de cuenta deben ser PDF.")
                    registro = importar_estado_cuenta(request.user, archivo, nombre_archivo=archivo.name)
                else:
                    registro, _ = importar_archivo_externo(request.user, "neat", archivo)
                if registro.estado != "PROCESADO":
                    raise ValueError(registro.mensaje)
            except Exception as error:
                messages.error(request, f"{archivo.name}: {error}")
            else:
                messages.success(request, f"{archivo.name}: datos disponibles para revisar; el saldo se conserva.")
    resumen = preparacion_cobros.resumen_preparacion(cuenta, periodo, anterior)
    if request.method == "POST" and request.POST.get("accion") != "importar":
        try:
            revision_cobros.preparar_calculo(cuenta, periodo, datos.get("referencia_id"),
                                            trasladar_ids=request.POST.getlist("trasladar_ids"),
                                            incluir_registros_guardados=request.POST.get("incluir_registros_guardados") == "1")
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        else:
            messages.success(request, "Borrador preparado con los movimientos asignados y las sugerencias disponibles. Elige qué aplicar, vincular como ya incluido o ignorar antes de confirmar.")
            return _volver_cuenta_cobro(cuenta, periodo)
    return render(request, "preparar_cobro.html", {
        "cuenta": cuenta, "periodo_cobro": periodo, "periodo_actual_cobro": f"{periodo:%Y-%m}",
        "anterior": anterior, "preparacion": resumen,
        "referencia_seleccionada": datos.get("referencia_id") or (str(anterior.pk) if anterior else ""),
        "referencias": cuenta.calculos.filter(estado="REFERENCIA", periodo__lte=periodo),
    })


@require_http_methods(["POST"])
@login_required
def guardar_revision_cobro(request, pk):
    calculo = get_object_or_404(CalculoCuentaPorCobrar, pk=pk, cuenta__user=request.user, estado="BORRADOR")
    decisiones = {}
    for partida in calculo.cuenta.partidas_revision.filter(resuelta_en__isnull=True, periodo__lte=calculo.periodo):
        campo = f"decision_{partida.pk}"
        if campo in request.POST:
            decisiones[str(partida.pk)] = {
                "decision": request.POST[campo], "conciliar_con": request.POST.get(f"conciliar_{partida.pk}"),
                "motivo": request.POST.get(f"motivo_{partida.pk}", "").strip(),
                "periodo": _cobro_periodo(request.POST.get(f"periodo_{partida.pk}"), calculo.periodo),
            }
    try:
        revision_cobros.guardar_decisiones(calculo, decisiones)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "Decisiones guardadas. Revisa el resultado antes de confirmar.")
    return _volver_cuenta_cobro(calculo.cuenta, calculo.periodo)


@require_http_methods(["POST"])
@login_required
def confirmar_revision_cobro(request, pk):
    calculo = get_object_or_404(CalculoCuentaPorCobrar, pk=pk, cuenta__user=request.user)
    try:
        revision_cobros.confirmar_calculo(calculo, request.POST.get("huella", ""))
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        messages.success(request, "Cálculo confirmado. El saldo y el texto se actualizaron; las versiones anteriores se conservan.")
    return _volver_cuenta_cobro(calculo.cuenta, calculo.periodo)


@require_http_methods(["POST"])
@login_required
def agregar_ajuste_cobro(request, pk):
    cuenta = get_object_or_404(CuentaPorCobrar, pk=pk, user=request.user)
    periodo = _cobro_periodo(request.POST.get("periodo"), primer_dia_mes(timezone.localdate()))
    try:
        monto = Decimal(request.POST.get("monto", "0"))
        if not monto.is_finite():
            raise InvalidOperation
        revision_cobros.proponer_partida(
            cuenta, "AJUSTE", periodo=periodo, monto=monto,
            fecha=_cobro_fecha(request.POST.get("fecha"), timezone.localdate()),
            descripcion=request.POST.get("descripcion", "Ajuste").strip(),
            motivo=request.POST.get("motivo", "").strip(),
            registrada_por_usuario=True,
        )
    except (ValidationError, InvalidOperation) as error:
        messages.error(request, " ".join(error.messages) if isinstance(error, ValidationError) else "El ajuste requiere un importe válido.")
    else:
        messages.success(request, "Ajuste pendiente de revisión en el mes abierto.")
    return _volver_cuenta_cobro(cuenta, periodo)


@login_required
def pasivos_view(request):
    """Liabilities dedicated page"""
    user = request.user
    
    # # Sync relevant products that don't have a Pasivo yet (e.g. existing TDC)
    # ensure_pasivos_for_products(user)
    
    resumen = resumen_patrimonio(user)
    tipo_cambio = resumen["tipo_cambio"]
    pasivos_all = resumen["pasivos"]
    for pasivo in pasivos_all:
        valor = resumen["valores_pasivos"][pasivo.pk]
        pasivo.monto_clp, pasivo.monto_usd = valor.clp, valor.usd
    total_pasivos = resumen["pasivos_total_clp"]
    
    # Group by tipo
    TIPO_ORDER = {'TDC': 0, 'CREDITO_HIPOTECARIO': 1, 'CREDITO_CONSUMO': 2, 'PRESTAMO': 3, 'OTRO': 4}
    TIPO_LABELS = dict(Pasivo.TIPO_PASIVO)
    
    grouped = {}
    for t_key, t_label in Pasivo.TIPO_PASIVO:
        items = [p for p in pasivos_all if p.tipo == t_key]
        if items:
            grouped[t_key] = {
                'label': t_label,
                'items': items,
                'total': sum(p.monto_clp for p in items),
            }
    
    # Activos for ratio
    activos_total = resumen["activos_total_clp"]
    
    ratio_da = (total_pasivos / activos_total * 100) if activos_total > 0 else 0
    
    # Available productos for linking
    productos = Producto.objects.filter(user=user, activo=True).select_related('banco')
    
    context = {
        'grouped': grouped,
        'total_pasivos': total_pasivos,
        'pasivos_count': len(pasivos_all),
        'ratio_da': ratio_da,
        'tipo_cambio': tipo_cambio,
        'productos': productos,
        'filas_por_revisar_moneda': resumen["filas_por_revisar_moneda"],
        'falta_tasa_usd': resumen["falta_tasa_usd"],
        'falta_tasa_uf': resumen["falta_tasa_uf"],
    }
    
    return render(request, 'pasivos.html', context)


@login_required
def departamentos(request):
    """Departments/Properties view"""
    user = request.user
    try:
        tipo_cambio = TipoCambio.objects.filter(fuente='mindicador.cl').latest('fecha')
        valor_uf = tipo_cambio.uf
    except TipoCambio.DoesNotExist:
        valor_uf = Decimal(0)
    
    departamentos_qs = Departamento.objects.filter(user=user).prefetch_related(
        'arrendatario', 'credito_hipotecario'
    )
    mostrar_inactivos = request.GET.get('inactivos') == '1'
    if not mostrar_inactivos:
        departamentos_qs = departamentos_qs.filter(activo=True)
    houm_ids = dict(
        VinculoPropiedadExterna.objects.filter(
            user=user, fuente="houm", activo=True
        ).values_list("departamento_id", "external_property_id")
    )
    for departamento in departamentos_qs:
        # Se expone como dato de presentación; la relación externa sigue siendo
        # la fuente única y conserva el historial de vinculaciones Houm.
        departamento.houm_id = houm_ids.get(departamento.id, "")
    
    total_arriendos = sum(
        d.arrendatario.monto_arriendo_clp 
        for d in departamentos_qs if d.activo and hasattr(d, 'arrendatario') and d.arrendatario.activo
    )
    cantidad_vigentes = sum(d.activo for d in departamentos_qs)
    promedio_arriendo = total_arriendos / cantidad_vigentes if cantidad_vigentes else 0
    
    total_valor_actual = sum(
        d.valor_actual_uf * valor_uf 
        for d in departamentos_qs if d.activo
    )
    
    bancos = Banco.objects.all().prefetch_related(
        Prefetch(
            "productos",
            queryset=Producto.objects.filter(user=user, reemplazado_por__isnull=True).order_by("orden", "nombre"),
            to_attr="productos_usuario",
        )
    )
    houm = resumen_houm_departamentos(user)
    
    # Calculate ROI metrics for each departamento
    deptos_con_roi = []
    for d in departamentos_qs:
        from pendientes.services.resumen import resumen_propiedad
        d.houm_resumen = houm['por_departamento'].get(d.id)
        d.pendientes_resumen = resumen_propiedad(d)
        info = {
            'depto': d,
            'valor_clp': d.valor_actual_uf * valor_uf,
            'houm': d.houm_resumen,
        }
        
        if hasattr(d, 'arrendatario'):
            renta_esperada = d.arrendatario.monto_arriendo_clp
            houm_department = info['houm']
            ingreso_real = (
                houm_department['actual']['deposito']
                if houm_department and houm_department['actual']
                else MovimientoManual.objects.filter(
                    user=user, departamento=d, direccion="INGRESO", naturaleza="OPERACIONAL"
                ).aggregate(avg=Avg('monto_clp'))['avg'] or Decimal(0)
            )
            
            info['renta_esperada'] = renta_esperada
            info['ingreso_real'] = ingreso_real
            info['costo_admin'] = (
                houm_department['actual']['administracion']
                if houm_department and houm_department['actual'] else Decimal(0)
            )
            info['deficit_houm'] = (
                houm_department['actual']['deficit']
                if houm_department and houm_department['actual'] else Decimal(0)
            )
            info['pct_admin'] = (info['costo_admin'] / renta_esperada * 100) if renta_esperada > 0 and ingreso_real > 0 else 0
            
            # New Metric: % Ingreso vs Renta Mensual
            info['pct_recaudacion'] = (ingreso_real / renta_esperada * 100) if renta_esperada > 0 else 0
            info['missing_pct'] = max(0, 100 - info['pct_recaudacion'])
            
            valor_actual_clp = d.valor_actual_uf * valor_uf
            if valor_actual_clp > 0 and ingreso_real > 0:
                info['roi_cashflow'] = float(ingreso_real * 12 / valor_actual_clp * 100)
            else:
                info['roi_cashflow'] = 0
            
            # ROI Amortization
            if hasattr(d, 'credito_hipotecario'):
                amort_mensual = d.credito_hipotecario.calcular_amortizacion_mensual() * valor_uf
                info['roi_amort'] = float(amort_mensual * 12 / valor_actual_clp * 100) if valor_actual_clp > 0 else 0
                
                # Dividend coverage metrics
                dividendo_clp = d.credito_hipotecario.cuota_uf * valor_uf
                info['dividendo_clp'] = dividendo_clp
                info['cobertura_dividendo'] = (ingreso_real / dividendo_clp * 100) if dividendo_clp > 0 else 0
                info['diferencia_dividendo'] = ingreso_real - dividendo_clp
                
                # NEW: Utilidad Patrimonial (Cash Flow + Amortización)
                # It represents the real gain after interests (Income - Interest)
                info['utilidad_patrimonial'] = info['diferencia_dividendo'] + amort_mensual
            else:
                info['roi_amort'] = 0
                info['dividendo_clp'] = 0
                info['cobertura_dividendo'] = 0
                info['diferencia_dividendo'] = 0
                info['utilidad_patrimonial'] = ingreso_real  # Without debt, entire income is gain (minus admin/tax costs already accounted in real income)
            
            info['roi_total'] = info['roi_cashflow'] + info['roi_amort']
        else:
            info['renta_esperada'] = 0
            info['ingreso_real'] = 0
            info['costo_admin'] = 0
            info['deficit_houm'] = (
                info['houm']['actual']['deficit']
                if info['houm'] and info['houm']['actual'] else Decimal(0)
            )
            info['pct_recaudacion'] = 0
            info['missing_pct'] = 100
            info['roi_cashflow'] = 0
            valor_actual_clp = d.valor_actual_uf * valor_uf
            if hasattr(d, 'credito_hipotecario'):
                amort_mensual = d.credito_hipotecario.calcular_amortizacion_mensual() * valor_uf
                dividendo_clp = d.credito_hipotecario.cuota_uf * valor_uf
                info['roi_amort'] = float(amort_mensual * 12 / valor_actual_clp * 100) if valor_actual_clp > 0 else 0
                info['dividendo_clp'] = dividendo_clp
                info['cobertura_dividendo'] = 0
                info['diferencia_dividendo'] = -dividendo_clp
                info['utilidad_patrimonial'] = -dividendo_clp + amort_mensual
            else:
                info['roi_amort'] = 0
                info['dividendo_clp'] = 0
                info['cobertura_dividendo'] = 0
                info['diferencia_dividendo'] = 0
                info['utilidad_patrimonial'] = 0
            info['roi_total'] = info['roi_amort']
        
        deptos_con_roi.append(info)

    # Productos CREDITO_HIPOTECARIO available from Entidades Financieras
    creditos_hip_productos = Producto.objects.filter(
        user=user, tipo='CREDITO_HIPOTECARIO', activo=True
    ).select_related('banco')

    # Calculate Global Portfolio Metrics for Cards
    roi_vigente = [i for i in deptos_con_roi if i['depto'].activo]
    total_ingreso_real = sum(i['ingreso_real'] for i in roi_vigente)
    total_renta_pactada = sum(i['renta_esperada'] for i in roi_vigente)
    total_cobertura_dividendo = sum(i['diferencia_dividendo'] for i in roi_vigente)
    total_utilidad_patrimonial = sum(i['utilidad_patrimonial'] for i in roi_vigente)
    
    total_amort_uf = sum(
        d.credito_hipotecario.calcular_amortizacion_mensual()
        for d in departamentos_qs if d.activo and hasattr(d, 'credito_hipotecario') and d.credito_hipotecario.activo
    )
    total_amort_clp = total_amort_uf * valor_uf
    
    # Average Cap Rate (weighted by current market value)
    total_value_clp = sum(i['valor_clp'] for i in roi_vigente)
    avg_cap_rate = float(total_ingreso_real * 12 / total_value_clp * 100) if total_value_clp > 0 else 0

    context = {
        'departamentos': departamentos_qs,
        'mostrar_inactivos': mostrar_inactivos,
        'deptos_con_roi': deptos_con_roi,
        'total_arriendos': total_renta_pactada,
        'total_ingreso_real': total_ingreso_real,
        'total_cobertura_dividendo': total_cobertura_dividendo,
        'total_utilidad_patrimonial': total_utilidad_patrimonial,
        'total_amort_clp': total_amort_clp,
        'total_amort_uf': total_amort_uf,
        'avg_cap_rate': avg_cap_rate,
        'promedio_arriendo': promedio_arriendo,
        'total_valor_actual': total_valor_actual,
        'tipo_cambio': valor_uf,
        'bancos': bancos,
        'creditos_hip_productos': creditos_hip_productos,
        'houm': houm,
        'houm_chart': {
            'labels': [row['etiqueta'] for row in houm['historial']],
            'renta': [float(row['renta']) for row in houm['historial']],
            'deposito': [float(row['deposito']) for row in houm['historial']],
            'administracion': [float(row['administracion']) for row in houm['historial']],
            'deficit': [float(row['deficit']) for row in houm['historial']],
        },
    }
    
    return render(request, 'departamentos.html', context)


@login_required
def departamentos_importar_documentos(request):
    """Compatibilidad para enlaces antiguos del importador municipal."""
    messages.info(
        request,
        "El importador de Aseo fue retirado. Registra la obligación y sus pagos manualmente en Pendientes.",
    )
    return redirect("dashboard_pendientes")


@login_required
def configuracion(request):
    """Configuration/Settings view"""
    user = request.user
    try:
        tipo_cambio = TipoCambio.objects.filter(fuente='mindicador.cl').latest('fecha')
    except TipoCambio.DoesNotExist:
        tipo_cambio = None
    bancos = Banco.objects.prefetch_related(Prefetch(
        "productos", queryset=Producto.objects.filter(user=user), to_attr="productos_usuario"
    ))
    categorias = CategoriaTematica.objects.filter(user=user, activa=True)
    from banca.services.presupuesto import detalle_promedios_categoria
    config_banca = ConfiguracionBanca.objects.filter(user=user).first()
    meses_promedio = config_banca.ventana_meses_ingreso if config_banca else 3
    config_notificaciones, _ = ConfiguracionNotificaciones.objects.get_or_create(user=user)

    market_integrations = None
    if request.user.is_staff:
        credentials = {item.provider: item for item in CredencialIntegracion.objects.all()}
        market_integrations = []
        descriptions = {
            CredencialIntegracion.PROVIDER_COINGECKO: "Precios, capitalización, supply y universo del scanner. La key es opcional para el plan gratuito.",
            CredencialIntegracion.PROVIDER_COINMARKETCAP: "Validación independiente de identidad y metadata de los proyectos.",
            CredencialIntegracion.PROVIDER_TOKENOMIST: "Calendarios de unlocks y eventos de dilución cuando se habilite el conector.",
            CredencialIntegracion.PROVIDER_BRAVE_SEARCH: "Descubrimiento de proyectos y fuentes públicas en internet. La búsqueda se limita a resultados citables y no inicia sesión en redes sociales.",
        }
        for provider, label in CredencialIntegracion.PROVIDER_CHOICES:
            credential = credentials.get(provider)
            market_integrations.append({
                "provider": provider,
                "label": label,
                "description": descriptions[provider],
                "credential": credential,
                "configured": bool(get_integration_api_key(provider)),
            })

    context = {
        'tipo_cambio': tipo_cambio,
        'bancos': bancos,
        'categorias': categorias,
        'apple_pay_keys': ApplePayApiKey.objects.filter(user=user).order_by('-created_at'),
        'apple_pay_new_key': request.session.pop('apple_pay_new_key', None),
        'market_integrations': market_integrations,
        'integration_encryption_ready': bool(getattr(settings, "BANCA_FERNET_KEY", "")),
        'notification_form': ConfiguracionNotificacionesForm(instance=config_notificaciones),
        'telegram_configurado': bool(
            config_notificaciones.telegram_bot_token_cifrado
            and config_notificaciones.telegram_chat_id_cifrado
        ),
        'config_notificaciones': config_notificaciones,
        'categorias_tematicas': CategoriaTematica.objects.filter(user=user, activa=True, aplica_a__in=("EGRESO", "AMBOS")),
        'productos_revision': Producto.objects.filter(user=user, requiere_revision=True).select_related('banco'),
        'limites_categoria': LimiteCategoria.objects.filter(user=user).select_related('categoria'),
        'promedios_categoria': detalle_promedios_categoria(user, meses_promedio),
        'meses_promedio': meses_promedio,
        'notificaciones_recientes': RegistroNotificacion.objects.filter(user=user)[:8],
    }
    
    return render(request, 'configuracion.html', context)


@require_http_methods(["POST"])
@login_required
def guardar_notificaciones(request):
    config, _ = ConfiguracionNotificaciones.objects.get_or_create(user=request.user)
    form = ConfiguracionNotificacionesForm(request.POST, instance=config)
    if not form.is_valid():
        detalle = "; ".join(
            error for errores in form.errors.values() for error in errores
        )
        messages.error(request, f"No se guardó la configuración: {detalle}")
        return redirect("configuracion")
    try:
        config = form.save()
    except RuntimeError as exc:
        messages.error(request, f"No se pudieron cifrar las credenciales: {exc}")
        return redirect("configuracion")
    if config.telegram_activo and not (
        config.telegram_bot_token_cifrado and config.telegram_chat_id_cifrado
    ):
        config.telegram_activo = False
        config.save(update_fields=["telegram_activo", "updated_at"])
        messages.warning(
            request,
            "Las preferencias se guardaron, pero Telegram seguirá desactivado hasta guardar token y chat ID.",
        )
    else:
        messages.success(request, "Notificaciones y credenciales personales guardadas.")
    return redirect("configuracion")


@require_http_methods(["POST"])
@login_required
def probar_notificaciones_telegram(request):
    config = ConfiguracionNotificaciones.objects.filter(user=request.user).first()
    if not config or not (
        config.telegram_bot_token_cifrado and config.telegram_chat_id_cifrado
    ):
        messages.error(request, "Guarda primero el token del bot y el chat ID.")
    elif probar_telegram(request.user):
        messages.success(request, "Mensaje de prueba enviado correctamente a tu chat.")
    else:
        messages.error(
            request,
            "Telegram rechazó el envío. Revisa el token, el chat ID y que hayas iniciado el bot.",
        )
    return redirect("configuracion")


@require_http_methods(["POST"])
@login_required
def retirar_notificaciones_telegram(request):
    config = ConfiguracionNotificaciones.objects.filter(user=request.user).first()
    if config:
        config.clear_telegram()
        config.save(
            update_fields=[
                "telegram_bot_token_cifrado",
                "telegram_chat_id_cifrado",
                "telegram_token_prefijo",
                "telegram_activo",
                "updated_at",
            ]
        )
    messages.success(request, "Las credenciales de Telegram fueron retiradas.")
    return redirect("configuracion")


@require_http_methods(["POST"])
@login_required
def guardar_limite_categoria(request):
    categoria = get_object_or_404(
        CategoriaTematica,
        pk=request.POST.get("categoria"),
        user=request.user,
        activa=True,
        aplica_a__in=("EGRESO", "AMBOS"),
    )
    try:
        monto = Decimal(request.POST.get("monto_mensual_clp", "0"))
    except (InvalidOperation, TypeError):
        monto = Decimal("0")
    if monto <= 0:
        messages.error(request, "El límite mensual debe ser mayor que cero.")
        return redirect("configuracion")
    LimiteCategoria.objects.update_or_create(
        user=request.user,
        categoria=categoria,
        defaults={"monto_mensual_clp": monto, "activo": True},
    )
    messages.success(request, f"Límite de {categoria.nombre} guardado.")
    return redirect("configuracion")


@require_http_methods(["POST"])
@login_required
def borrar_limite_categoria(request, pk):
    limite = get_object_or_404(LimiteCategoria, pk=pk, user=request.user)
    nombre = limite.categoria.nombre
    limite.delete()
    messages.success(request, f"Límite de {nombre} eliminado.")
    return redirect("configuracion")


@user_passes_test(lambda user: user.is_staff)
@require_http_methods(["POST"])
def guardar_market_api_key(request, provider):
    valid_providers = {choice[0] for choice in CredencialIntegracion.PROVIDER_CHOICES}
    if provider not in valid_providers:
        messages.error(request, "Proveedor de mercado no reconocido.")
        return redirect("configuracion")
    api_key = request.POST.get("api_key", "").strip()
    if len(api_key) < 8 or len(api_key) > 500:
        messages.error(request, "La API key debe tener entre 8 y 500 caracteres.")
        return redirect("configuracion")
    credential, _ = CredencialIntegracion.objects.get_or_create(
        provider=provider,
    )
    try:
        credential.set_api_key(api_key)
    except RuntimeError as exc:
        messages.error(request, f"No se pudo cifrar la API key: {exc}")
        return redirect("configuracion")
    credential.active = True
    credential.updated_by = request.user
    credential.save()
    messages.success(request, f"API key de {credential.get_provider_display()} guardada y cifrada.")
    return redirect("configuracion")


@user_passes_test(lambda user: user.is_staff)
@require_http_methods(["POST"])
def retirar_market_api_key(request, provider):
    valid_providers = {choice[0] for choice in CredencialIntegracion.PROVIDER_CHOICES}
    if provider not in valid_providers:
        messages.error(request, "Proveedor de mercado no reconocido.")
        return redirect("configuracion")
    credential = CredencialIntegracion.objects.filter(
        provider=provider,
    ).first()
    if credential:
        credential.api_key_cifrada = None
        credential.key_prefix = ""
        credential.active = False
        credential.updated_by = request.user
        credential.save(update_fields=["api_key_cifrada", "key_prefix", "active", "updated_by", "updated_at"])
    messages.success(request, "La API key guardada en el panel fue retirada.")
    return redirect("configuracion")


@require_http_methods(["POST"])
@login_required
def crear_apple_pay_key(request):
    """Crea una llave para el Atajo y conserva el secreto en sesión una sola vez."""
    key, secreto = ApplePayApiKey.crear(
        request.user,
        nombre=request.POST.get('nombre', 'Atajo iPhone'),
    )
    request.session['apple_pay_new_key'] = {
        'id': key.id,
        'nombre': key.nombre,
        'prefijo': key.prefijo,
        'secret': secreto,
    }
    messages.success(request, 'API key creada. Cópiala ahora: no volverá a mostrarse.')
    return redirect('configuracion')


@require_http_methods(["POST"])
@login_required
def revocar_apple_pay_key(request, pk):
    key = ApplePayApiKey.objects.filter(pk=pk, user=request.user).first()
    if not key:
        from django.http import Http404

        raise Http404
    key.activo = False
    key.revocado_at = timezone.now()
    key.save(update_fields=['activo', 'revocado_at', 'updated_at'])
    messages.success(request, f'API key «{key.nombre}» revocada.')
    return redirect('configuracion')


# ===================== ACTIVOS CRUD =====================

def _aplicar_saldo_original(request, objeto):
    """Read the single-source-currency form, while retaining old POST compatibility."""
    raw = request.POST.get("monto_original")
    moneda = request.POST.get("moneda_original") or getattr(objeto, "moneda_original", None) or "CLP"
    if raw is None and ("monto_clp" in request.POST or "monto_usd" in request.POST):
        try:
            clp = Decimal(request.POST.get("monto_clp") or "0")
            usd = Decimal(request.POST.get("monto_usd") or "0")
        except InvalidOperation:
            raise ValueError("El monto debe ser numérico.")
        if not clp.is_finite() or not usd.is_finite() or clp < 0 or usd < 0:
            raise ValueError("El monto no puede ser negativo.")
        if clp and usd:
            objeto.monto_clp = normalizar_monto(clp)
            objeto.monto_usd = normalizar_monto(usd)
            objeto.monto_original = objeto.moneda_original = None
            objeto.requiere_revision_moneda = True
            return
        raw, moneda = str(usd if usd else clp), "USD" if usd else "CLP"
    try:
        monto = Decimal(raw or "0")
    except InvalidOperation:
        raise ValueError("El monto debe ser numérico.")
    if not monto.is_finite() or monto < 0:
        raise ValueError("El monto no puede ser negativo.")
    if moneda not in {"CLP", "USD"}:
        raise ValueError("Selecciona CLP o USD como moneda del saldo.")
    tipo_cambio = tipo_cambio_para()
    objeto.asignar_saldo_original(monto, moneda, tipo_cambio)


def _redirigir_con_error_saldo(request, mensaje, url):
    messages.error(request, mensaje)
    return redirect(url)

@require_http_methods(["POST"])
@login_required
def crear_activo(request):
    activo = Activo(
        user=request.user,
        nombre=request.POST.get('nombre', 'Nuevo Activo'),
        tipo=request.POST.get('tipo', 'OTRO'),
        horizonte_temporal=request.POST.get('horizonte_temporal', 'EFECTIVO'),
        es_liquido=request.POST.get('es_liquido') == 'on',
        notas=request.POST.get('notas', ''),
    )
    try:
        _aplicar_saldo_original(request, activo)
    except ValueError as error:
        return _redirigir_con_error_saldo(request, str(error), "activos")
    activo.save()
    next_url = request.POST.get('next', 'activos')
    return redirect(next_url)


@require_http_methods(["POST"])
@login_required
def sincronizar_activo(request, pk):
    activo = get_object_or_404(Activo, pk=pk, user=request.user, activo=True)
    origen = origen_activo(activo)
    if not origen.credencial:
        messages.error(request, 'Revisa la cuenta y su conexión en Banca → Configuración.')
        return redirect('activos')
    from banca.services.sync import run_sync

    try:
        log = run_sync(origen.credencial, modo='full')
    except Exception:
        messages.error(request, 'No se pudo sincronizar la fuente. Revisa su configuración.')
        return redirect('activos')
    if log.exito:
        activo.refresh_from_db()
        if activo.updated_at >= log.iniciado:
            messages.success(request, f'{origen.etiqueta}: sincronización completada.')
        else:
            messages.warning(request, f'{origen.etiqueta}: la fuente no informó un nuevo saldo para este activo. Se conserva el anterior.')
    else:
        messages.error(request, 'No se pudo completar la sincronización. Revisa el historial en Banca → Sync.')
    return redirect('activos')


@require_http_methods(["POST"])
@login_required
def editar_activo(request, pk):
    activo = get_object_or_404(Activo, pk=pk, user=request.user)
    if hasattr(activo, 'prestamo_recuperable'):
        return _redirigir_con_error_saldo(request, 'Edita el capital y los abonos desde Bicicletas.', 'activos')
    if origen_activo(activo).externo:
        return _redirigir_con_error_saldo(request, 'Este saldo se actualiza con Sincronizar desde su fuente.', 'activos')
    activo.nombre = request.POST.get('nombre', activo.nombre)
    activo.tipo = request.POST.get('tipo', activo.tipo)
    activo.horizonte_temporal = request.POST.get('horizonte_temporal', activo.horizonte_temporal)
    activo.es_liquido = request.POST.get('es_liquido') == 'on'
    try:
        _aplicar_saldo_original(request, activo)
    except ValueError as error:
        return _redirigir_con_error_saldo(request, str(error), "activos")
    activo.notas = request.POST.get('notas', activo.notas)
    activo.save()
    next_url = request.POST.get('next', 'activos')
    return redirect(next_url)


@require_http_methods(["POST"])
@login_required
def borrar_activo(request, pk):
    activo = get_object_or_404(Activo, pk=pk, user=request.user)
    if hasattr(activo, 'prestamo_recuperable'):
        return _redirigir_con_error_saldo(request, 'Desactiva la bicicleta para conservar su historial.', 'activos')
    if origen_activo(activo).externo:
        return _redirigir_con_error_saldo(request, 'Este activo está vinculado a una fuente de sincronización.', 'activos')
    activo.delete()
    next_url = request.POST.get('next', 'activos')
    return redirect(next_url)


# ===================== PASIVOS CRUD =====================

@require_http_methods(["POST"])
@login_required
def crear_pasivo(request):
    producto_id = request.POST.get('producto_id')
    producto = get_object_or_404(Producto, id=producto_id, user=request.user) if producto_id else None
    
    pasivo = Pasivo(
        user=request.user,
        nombre=request.POST.get('nombre', 'Nueva Deuda'),
        tipo=request.POST.get('tipo', 'OTRO'),
        producto=producto,
        notas=request.POST.get('notas', ''),
    )
    try:
        _aplicar_saldo_original(request, pasivo)
    except ValueError as error:
        return _redirigir_con_error_saldo(request, str(error), "pasivos")
    pasivo.save()
    next_url = request.POST.get('next', 'pasivos')
    return redirect(next_url)


@require_http_methods(["POST"])
@login_required
def editar_pasivo(request, pk):
    pasivo = get_object_or_404(Pasivo, pk=pk, user=request.user)
    pasivo.nombre = request.POST.get('nombre', pasivo.nombre)
    pasivo.tipo = request.POST.get('tipo', pasivo.tipo)
    try:
        _aplicar_saldo_original(request, pasivo)
    except ValueError as error:
        return _redirigir_con_error_saldo(request, str(error), "pasivos")
    pasivo.notas = request.POST.get('notas', pasivo.notas)
    pasivo.save()
    next_url = request.POST.get('next', 'pasivos')
    return redirect(next_url)


@require_http_methods(["POST"])
@login_required
def borrar_pasivo(request, pk):
    pasivo = Pasivo.objects.get(pk=pk, user=request.user)
    # Don't delete if linked to a Producto (Entidad Financiera)
    if pasivo.producto:
        return redirect('pasivos')
    pasivo.delete()
    next_url = request.POST.get('next', 'pasivos')
    return redirect(next_url)


# ===================== DEPARTAMENTOS CRUD =====================

def _guardar_id_houm(user, departamento, valor):
    """Actualiza el vínculo Houm editable sin duplicar la relación externa."""
    external_id = (valor or "").strip()
    actuales = VinculoPropiedadExterna.objects.filter(
        user=user, fuente="houm", departamento=departamento, activo=True
    )
    if not external_id:
        actuales.update(activo=False)
        return

    conflicto = (
        VinculoPropiedadExterna.objects.filter(
            user=user, fuente="houm", external_property_id=external_id, activo=True
        )
        .exclude(departamento=departamento)
        .select_related("departamento")
        .first()
    )
    if conflicto:
        raise ValueError(
            f"El ID Houm {external_id} ya está vinculado a {conflicto.departamento.codigo}."
        )

    vinculo = VinculoPropiedadExterna.objects.filter(
        user=user, fuente="houm", external_property_id=external_id
    ).first()
    if vinculo is None:
        vinculo = VinculoPropiedadExterna(
            user=user,
            fuente="houm",
            external_property_id=external_id,
            departamento=departamento,
            nombre_externo=departamento.codigo,
            activo=True,
        )
    else:
        vinculo.departamento = departamento
        vinculo.activo = True
        if not vinculo.nombre_externo:
            vinculo.nombre_externo = departamento.codigo
    vinculo.save()
    actuales.exclude(pk=vinculo.pk).update(activo=False)

@require_http_methods(["POST"])
@login_required
def crear_departamento(request):
    depto = Departamento(user=request.user)
    depto.codigo = request.POST.get('codigo')
    depto.piso = request.POST.get('piso')
    depto.metros_cuadrados = request.POST.get('metros_cuadrados')
    depto.valor_compra_uf = request.POST.get('valor_compra_uf')
    depto.valor_actual_uf = request.POST.get('valor_actual_uf')
    depto.save()
    depto.comuna = request.POST.get('comuna', '').strip()
    depto.rol_sii = request.POST.get('rol_sii', '').strip()
    depto.save(update_fields=['comuna', 'rol_sii', 'updated_at'])
    try:
        _guardar_id_houm(request.user, depto, request.POST.get('houm_id'))
    except ValueError as exc:
        messages.error(request, str(exc))
    
    return redirect('departamentos')


@require_http_methods(["POST"])
@login_required
def editar_departamento(request, pk):
    depto = Departamento.objects.get(pk=pk, user=request.user)
    depto.codigo = request.POST.get('codigo')
    depto.piso = request.POST.get('piso')
    depto.metros_cuadrados = request.POST.get('metros_cuadrados')
    depto.valor_compra_uf = request.POST.get('valor_compra_uf')
    depto.valor_actual_uf = request.POST.get('valor_actual_uf')
    depto.save()
    depto.comuna = request.POST.get('comuna', '').strip()
    depto.rol_sii = request.POST.get('rol_sii', '').strip()
    depto.save(update_fields=['comuna', 'rol_sii', 'updated_at'])
    try:
        _guardar_id_houm(request.user, depto, request.POST.get('houm_id'))
    except ValueError as exc:
        messages.error(request, str(exc))
    
    return redirect('departamentos')


@require_http_methods(["POST"])
@login_required
def borrar_departamento(request, pk):
    depto = Departamento.objects.get(pk=pk, user=request.user)
    depto.delete()
    return redirect('departamentos')


@require_http_methods(['POST'])
@login_required
def alternar_departamento(request, pk):
    depto = get_object_or_404(Departamento, pk=pk, user=request.user)
    depto.activo = not depto.activo
    depto.save(update_fields=['activo', 'updated_at'])
    messages.success(request, 'Propiedad reactivada.' if depto.activo else 'Propiedad inactiva. Se conservan sus documentos y su deuda pendiente.')
    return redirect(reverse('departamentos') + '?inactivos=1')


@login_required
def estacionamientos(request):
    """Manage parking spaces"""
    user = request.user
    estacionamientos = Estacionamiento.objects.filter(departamento__user=user)
    
    if request.method == "POST":
        eid = request.POST.get('id')
        est = Estacionamiento.objects.get(id=eid, departamento__user=user)
        est.asignado_a_arrendatario = request.POST.get('asignado') == 'on'
        est.notas = request.POST.get('notas', '')
        
        aid = request.POST.get('arrendatario')
        if aid:
            est.arrendatario = Arrendatario.objects.get(id=aid)
        else:
            est.arrendatario = None
        est.save()
        return redirect('estacionamientos')

    arrendatarios = Arrendatario.objects.filter(departamento__user=user)
    
    context = {
        'estacionamientos': estacionamientos,
        'arrendatarios': arrendatarios,
    }
    return render(request, 'estacionamientos.html', context)


# ===================== ARRENDATARIOS CRUD =====================

@require_http_methods(["POST"])
@login_required
def crear_arrendatario(request, depto_pk):
    depto = Departamento.objects.get(pk=depto_pk, user=request.user)
    Arrendatario.objects.create(
        departamento=depto,
        nombre=request.POST.get('nombre'),
        rut=request.POST.get('rut'),
        fecha_inicio_contrato=request.POST.get('fecha_inicio_contrato'),
        monto_arriendo_clp=request.POST.get('monto_arriendo_clp', 0),
        monto_arriendo_usd=request.POST.get('monto_arriendo_usd', 0),
        telefono=request.POST.get('telefono', ''),
        email=request.POST.get('email', '')
    )
    return redirect('departamentos')


@require_http_methods(["POST"])
@login_required
def editar_arrendatario(request, pk):
    arrendatario = Arrendatario.objects.get(pk=pk, departamento__user=request.user)
    arrendatario.nombre = request.POST.get('nombre', arrendatario.nombre)
    arrendatario.rut = request.POST.get('rut', arrendatario.rut)
    arrendatario.fecha_inicio_contrato = request.POST.get('fecha_inicio_contrato', arrendatario.fecha_inicio_contrato)
    arrendatario.monto_arriendo_clp = request.POST.get('monto_arriendo_clp', arrendatario.monto_arriendo_clp)
    arrendatario.monto_arriendo_usd = request.POST.get('monto_arriendo_usd', arrendatario.monto_arriendo_usd)
    arrendatario.telefono = request.POST.get('telefono', arrendatario.telefono)
    arrendatario.email = request.POST.get('email', arrendatario.email)
    arrendatario.save()
    return redirect('departamentos')


@require_http_methods(["POST"])
@login_required
def borrar_arrendatario(request, pk):
    arrendatario = Arrendatario.objects.get(pk=pk, departamento__user=request.user)
    arrendatario.delete()
    return redirect('departamentos')


# ===================== CREDITO HIPOTECARIO CRUD =====================

@require_http_methods(["POST"])
@login_required
def crear_credito(request, depto_pk):
    depto = Departamento.objects.get(pk=depto_pk, user=request.user)
    
    # Link to existing Producto (CREDITO_HIPOTECARIO) from Entidades Financieras
    producto_id = request.POST.get('producto_id')
    banco_id = request.POST.get('banco_id')
    
    producto = None
    banco = None
    if producto_id:
        producto = Producto.objects.get(id=producto_id, user=request.user)
        banco = producto.banco
    elif banco_id:
        banco = Banco.objects.get(id=banco_id)
    
    CreditoHipotecario.objects.create(
        departamento=depto,
        producto=producto,
        banco=banco,
        monto_original_uf=request.POST.get('monto_original_uf', 0),
        tasa_anual=request.POST.get('tasa_anual', 0),
        plazo_anos=request.POST.get('plazo_anos', 0),
        tipo_tasa=request.POST.get('tipo_tasa', 'FIJA'),
        cuota_uf=request.POST.get('cuota_uf', 0),
        saldo_actual_uf=request.POST.get('saldo_actual_uf', 0),
        cuotas_totales=request.POST.get('cuotas_totales', 0),
        cuotas_pagadas=request.POST.get('cuotas_pagadas', 0)
    )
    
    fecha_inicio = request.POST.get('fecha_inicio')
    fecha_ultima_cuota = request.POST.get('fecha_ultima_cuota')
    plazo_anos = request.POST.get('plazo_anos')
    
    if fecha_inicio: depto.fecha_inicio = fecha_inicio
    if fecha_ultima_cuota: depto.fecha_ultima_cuota = fecha_ultima_cuota
    if plazo_anos: depto.plazo_anos = int(plazo_anos)
    depto.save(update_fields=['fecha_inicio', 'fecha_ultima_cuota', 'plazo_anos'])
    
    return redirect('departamentos')


@require_http_methods(["POST"])
@login_required
def editar_credito(request, pk):
    credito = CreditoHipotecario.objects.get(pk=pk, departamento__user=request.user)
    
    # Update producto link (from Entidades Financieras)
    producto_id = request.POST.get('producto_id')
    if producto_id:
        credito.producto_id = producto_id
        credito.banco = Producto.objects.get(id=producto_id, user=request.user).banco
    
    credito.monto_original_uf = request.POST.get('monto_original_uf', credito.monto_original_uf)
    credito.tasa_anual = request.POST.get('tasa_anual', credito.tasa_anual)
    credito.plazo_anos = request.POST.get('plazo_anos', credito.plazo_anos)
    credito.tipo_tasa = request.POST.get('tipo_tasa', credito.tipo_tasa)
    credito.cuota_uf = request.POST.get('cuota_uf', credito.cuota_uf)
    credito.saldo_actual_uf = request.POST.get('saldo_actual_uf', credito.saldo_actual_uf)
    credito.cuotas_totales = request.POST.get('cuotas_totales', credito.cuotas_totales)
    credito.cuotas_pagadas = request.POST.get('cuotas_pagadas', credito.cuotas_pagadas)
    credito.save()
    
    depto = credito.departamento
    fecha_inicio = request.POST.get('fecha_inicio')
    fecha_ultima_cuota = request.POST.get('fecha_ultima_cuota')
    plazo_anos = request.POST.get('plazo_anos')
    
    depto.fecha_inicio = fecha_inicio if fecha_inicio else None
    depto.fecha_ultima_cuota = fecha_ultima_cuota if fecha_ultima_cuota else None
    depto.plazo_anos = int(plazo_anos) if plazo_anos else None
    depto.save(update_fields=['fecha_inicio', 'fecha_ultima_cuota', 'plazo_anos'])
    
    return redirect('departamentos')


@require_http_methods(["POST"])
@login_required
def borrar_credito(request, pk):
    credito = CreditoHipotecario.objects.get(pk=pk, departamento__user=request.user)
    depto = credito.departamento
    credito.delete()
    
    depto.fecha_inicio = None
    depto.fecha_ultima_cuota = None
    depto.plazo_anos = None
    depto.save()
    
    return redirect('departamentos')


# ===================== GASTOS CRUD =====================

@login_required
def bulk_gastos(request):
    """Carga rápida: totales mensuales convertidos a movimientos manuales."""
    from calendar import monthrange
    from gastos.services import convertir_a_clp

    user = request.user
    if request.method == "POST":
        year = int(request.POST.get('year'))
        month = int(request.POST.get('month'))
        periodo = date(year, month, 1)
        fecha = date(year, month, monthrange(year, month)[1])
        modo_resumen = request.POST.get("modo_resumen", "TOTAL")
        if modo_resumen not in {"TOTAL", "ADICIONAL"}:
            return HttpResponse("Modo de resumen inválido.", status=400)
        for key, value in request.POST.items():
            if key.startswith('cat_'):
                cat_id = key.split('_')[1]
                monto = value.strip()
                if monto:
                    decimal_monto = Decimal(monto.replace(',', '.'))
                    if decimal_monto <= 0:
                        continue
                    categoria = CategoriaTematica.objects.get(id=cat_id, user=user, activa=True)
                    direccion = "INGRESO" if categoria.aplica_a == "INGRESO" else "EGRESO"
                    monto_clp, tasa, estimado = convertir_a_clp(decimal_monto, "CLP", fecha)
                    MovimientoManual.objects.update_or_create(
                        user=user, periodo=periodo, categoria=categoria,
                        direccion=direccion, es_resumen_mensual=True,
                        defaults={
                            "fecha": fecha, "monto": decimal_monto, "moneda": "CLP",
                            "monto_clp": monto_clp, "tipo_cambio": tasa,
                            "monto_clp_estimado": estimado, "naturaleza": "OPERACIONAL",
                            "descripcion": categoria.nombre,
                            "modo_resumen": modo_resumen,
                        },
                    )
        return redirect(f'{reverse("gastos-table")}?year={year}&month={month}')

    today = date.today()
    last_month_date = today.replace(day=1) - timedelta(days=1)
    
    categorias = CategoriaTematica.objects.filter(user=user, activa=True).order_by("aplica_a", "orden", "nombre")
    context = {
        'years': range(today.year - 2, today.year + 2),
        'default_year': last_month_date.year,
        'default_month': last_month_date.month,
        'categorias': categorias,
    }
    return render(request, 'bulk_gastos_modal.html', context)


@login_required
def get_category_data(request, cat_pk):
    """Histórico unificado de una categoría."""
    from django.http import JsonResponse
    user = request.user
    categoria = CategoriaTematica.objects.get(pk=cat_pk, user=user)
    hoy = date.today().replace(day=1)
    periodos = []
    cursor = hoy
    for _ in range(12):
        periodos.append(cursor)
        cursor = date(cursor.year - (cursor.month == 1), 12 if cursor.month == 1 else cursor.month - 1, 1)
    periodos.reverse()
    data = {
        'labels': [f"{p.month:02d}-{p.year}" for p in periodos],
        'values': [str(sum((g["total_clp"] for g in resumen_gastos(
            user, p, p, "consumo", {"categoria": str(cat_pk)}
        )["categorias"]), Decimal("0"))) for p in periodos],
        'name': categoria.nombre
    }
    return JsonResponse(data)


# ===================== CATEGORIAS CRUD =====================

@require_http_methods(["POST"])
@login_required
def crear_categoria(request):
    nombre = request.POST.get('nombre')
    if nombre:
        CategoriaTematica.objects.create(
            user=request.user,
            nombre=nombre,
            aplica_a=request.POST.get('aplica_a', request.POST.get('tipo', 'EGRESO')),
            esencialidad_defecto=request.POST.get('esencialidad_defecto', 'OCIO'),
            origen="USUARIO",
            activa=True,
        )
    next_url = request.POST.get('next', 'configuracion')
    return redirect(next_url)


@require_http_methods(["POST"])
@login_required
def editar_categoria(request, pk):
    cat = CategoriaTematica.objects.get(pk=pk, user=request.user)
    cat.nombre = request.POST.get('nombre', cat.nombre)
    cat.aplica_a = request.POST.get('aplica_a', cat.aplica_a)
    cat.esencialidad_defecto = request.POST.get('esencialidad_defecto', cat.esencialidad_defecto)
    cat.save()
    next_url = request.POST.get('next', 'configuracion')
    return redirect(next_url)


@require_http_methods(["POST"])
@login_required
def borrar_categoria(request, pk):
    cat = CategoriaTematica.objects.get(pk=pk, user=request.user)
    cat.activa = False
    cat.save(update_fields=["activa"])
    next_url = request.POST.get('next', 'configuracion')
    return redirect(next_url)


# ===================== BANCOS CRUD =====================

@require_http_methods(["POST"])
@login_required
def crear_banco(request):
    if not request.user.is_staff:
        raise PermissionDenied
    nombre = request.POST.get('nombre')
    notas = request.POST.get('notas', '')
    if nombre:
        Banco.objects.create(
            nombre=nombre,
            notas=notas,
            activo=True
        )
    return redirect('configuracion')


@require_http_methods(["POST"])
@login_required
def editar_banco(request, pk):
    if not request.user.is_staff:
        raise PermissionDenied
    banco = get_object_or_404(Banco, pk=pk)
    banco.nombre = request.POST.get('nombre', banco.nombre)
    banco.notas = request.POST.get('notas', banco.notas)
    banco.save()
    return redirect('configuracion')


@require_http_methods(["POST"])
@login_required
@transaction.atomic
def borrar_banco(request, pk):
    if not request.user.is_staff:
        raise PermissionDenied
    banco = get_object_or_404(Banco.objects.select_for_update(), pk=pk)
    if banco_en_uso(banco):
        messages.error(request, "No se puede eliminar una entidad financiera en uso. Conserva sus relaciones y desactívala desde la administración si corresponde.")
        return redirect('configuracion')
    try:
        banco.delete()
    except ProtectedError:
        messages.error(request, "No se puede eliminar una entidad financiera en uso.")
    return redirect('configuracion')


# ===================== CALENDARIO =====================

@login_required
def calendario(request):
    from gastos.models import GastoProgramado
    from departamentos.models import Departamento
    import calendar
    from datetime import date
    
    try:
        year = int(request.GET.get('year', date.today().year))
        month = int(request.GET.get('month', date.today().month))
        date(year, month, 1)
    except (ValueError, TypeError):
        year = date.today().year
        month = date.today().month

    cal = calendar.Calendar(firstweekday=6)
    month_days = cal.monthdayscalendar(year, month)
    month_name = calendar.month_name[month].capitalize()

    departamentos_cal = request.user.departamentos.filter(fecha_ultima_cuota__gte=date(year, month, 1))
    
    gastos_programados = GastoProgramado.objects.filter(
        user=request.user, activo=True, regla_pendiente__isnull=True
    )
    ahorro_mensual_total = sum(g.ahorro_mensual for g in gastos_programados)
    
    grid_gastos = []
    current_date = date(year, month, 1)
    
    for g in gastos_programados:
        if g.fecha_inicio <= current_date:
            month_diff = (year - g.fecha_inicio.year) * 12 + (month - g.fecha_inicio.month)
            show = False
            if g.frecuencia == 'MENSUAL': show = True
            elif g.frecuencia == 'BIMESTRAL' and month_diff % 2 == 0: show = True
            elif g.frecuencia == 'TRIMESTRAL' and month_diff % 3 == 0: show = True
            elif g.frecuencia == 'SEMESTRAL' and month_diff % 6 == 0: show = True
            elif g.frecuencia == 'ANUAL' and month_diff % 12 == 0: show = True
            
            if show:
                g.dia_cobro = g.fecha_inicio.day
                grid_gastos.append(g)
    
    # Sort grid_gastos by day of the month for the "Next Payment" card
    grid_gastos.sort(key=lambda x: x.dia_cobro)
    vencimientos_tarjetas = vencimientos_tdc(request.user, year, month)
    from banca.services.calendario_externo import eventos_externos_calendario

    eventos_externos = eventos_externos_calendario(request.user, year, month)
    from pendientes.services.calendario import materializar_usuario
    from pendientes.models import OcurrenciaPendiente
    materializar_usuario(request.user)
    pendientes_calendario = OcurrenciaPendiente.objects.filter(
        regla__user=request.user,
        regla__activa=True,
        fecha_vencimiento__year=year,
        fecha_vencimiento__month=month,
    ).select_related('regla', 'regla__departamento')
    eventos_calendario = [
        {
            'dia_cobro': gasto.dia_cobro,
            'nombre': gasto.nombre,
            'monto': gasto.monto,
            'tipo': 'programado',
        }
        for gasto in grid_gastos
    ] + vencimientos_tarjetas + eventos_externos
    eventos_calendario += [
        {
            'dia_cobro': ocurrencia.fecha_vencimiento.day,
            'nombre': ocurrencia.regla.nombre,
            'monto': ocurrencia.monto_esperado,
            'tipo': 'pendiente',
            'estado': ocurrencia.estado,
        }
        for ocurrencia in pendientes_calendario
        if ocurrencia.estado == 'PENDIENTE'
    ]
    eventos_calendario.sort(key=lambda evento: (evento['dia_cobro'], evento['tipo']))

    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    context = {
        'year': year,
        'month': month,
        'month_name': month_name,
        'month_days': month_days,
        'departamentos': departamentos_cal,
        'gastos_programados': gastos_programados,
        'ahorro_mensual_total': ahorro_mensual_total,
        'grid_gastos': grid_gastos,
        'vencimientos_tarjetas': vencimientos_tarjetas,
        'eventos_externos': eventos_externos,
        'pendientes_calendario': pendientes_calendario,
        'today': date.today(),
        'proximo_vencimiento': eventos_calendario[0] if eventos_calendario else None,
        'nav': {
            'prev_m': prev_month, 'prev_y': prev_year,
            'next_m': next_month, 'next_y': next_year
        }
    }
    return render(request, 'calendario.html', context)


@require_http_methods(["POST"])
@login_required
def crear_gasto_programado(request):
    from gastos.models import GastoProgramado
    GastoProgramado.objects.create(
        user=request.user,
        nombre=request.POST.get('nombre'),
        monto=request.POST.get('monto'),
        fecha_inicio=request.POST.get('fecha_inicio'),
        frecuencia=request.POST.get('frecuencia'),
        notas=request.POST.get('notas', ''),
        activo=request.POST.get('activo') == 'on' if 'activo' in request.POST else True
    )
    return redirect('calendario')


@require_http_methods(["POST"])
@login_required
def editar_gasto_programado(request, pk):
    from gastos.models import GastoProgramado
    gasto = GastoProgramado.objects.get(pk=pk, user=request.user)
    gasto.nombre = request.POST.get('nombre', gasto.nombre)
    gasto.monto = request.POST.get('monto', gasto.monto)
    gasto.fecha_inicio = request.POST.get('fecha_inicio', gasto.fecha_inicio)
    gasto.frecuencia = request.POST.get('frecuencia', gasto.frecuencia)
    gasto.notas = request.POST.get('notas', gasto.notas)
    if 'activo' in request.POST:
        gasto.activo = request.POST.get('activo') == 'on'
    gasto.save()
    return redirect('calendario')


@require_http_methods(["POST"])
@login_required
def borrar_gasto_programado(request, pk):
    from gastos.models import GastoProgramado
    gasto = GastoProgramado.objects.get(pk=pk, user=request.user)
    gasto.delete()
    return redirect('calendario')


# ===================== PRODUCTOS CRUD =====================

@require_http_methods(["POST"])
@login_required
def crear_producto(request):
    banco_id = request.POST.get('banco_id')
    nombre = request.POST.get('nombre')
    tipo = request.POST.get('tipo', 'TDC')
    tiene_cupo_usd = request.POST.get('tiene_cupo_usd') == 'on'
    dia_cobro = request.POST.get('dia_cobro')
    dia_cobro = int(dia_cobro) if dia_cobro and dia_cobro.isdigit() else None
    
    prod = Producto.objects.create(
        user=request.user,
        banco_id=banco_id,
        nombre=nombre,
        tipo=tipo,
        ultimos4=request.POST.get('ultimos4', '')[-4:],
        origen='MANUAL',
        tiene_cupo_usd=tiene_cupo_usd,
        contabilizar=True,
        dia_cobro=dia_cobro,
        activo=True
    )
    
    banco = Banco.objects.get(id=banco_id)
    
    # Auto-create Pasivo for relevant products
    if tipo in ['CREDITO_HIPOTECARIO', 'CREDITO_CONSUMO', 'TDC']:
        Pasivo.objects.get_or_create(
            user=request.user,
            producto=prod,
            defaults={
                'nombre': f"{banco.nombre} - {nombre}",
                'tipo': tipo,
                'monto_clp': 0,
                'monto_usd': 0,
                'activo': True,
                'notas': f"Auto-generado desde Entidad Financiera: {banco.nombre} - {nombre}",
            }
        )
    
    return redirect('configuracion')


@require_http_methods(["POST"])
@login_required
def editar_producto(request, pk):
    prod = Producto.objects.get(pk=pk, user=request.user)
    prod.banco_id = request.POST.get('banco_id', prod.banco_id)
    prod.nombre = request.POST.get('nombre', prod.nombre)
    prod.tipo = request.POST.get('tipo', prod.tipo)
    prod.ultimos4 = request.POST.get('ultimos4', prod.ultimos4)[-4:]
    
    prod.tiene_cupo_usd = request.POST.get('tiene_cupo_usd') == 'on'
    dia_cobro = request.POST.get('dia_cobro')
    prod.dia_cobro = int(dia_cobro) if dia_cobro and dia_cobro.isdigit() else None
    
    prod.save()
    
    # Ensure Pasivo exists if type was changed to one that requires it or if it didn't exist
    if prod.tipo in ['CREDITO_HIPOTECARIO', 'CREDITO_CONSUMO', 'TDC']:
        ensure_pasivos_for_products(request.user)
    
    return redirect('configuracion')


@require_http_methods(["POST"])
@login_required
def borrar_producto(request, pk):
    prod = Producto.objects.get(pk=pk, user=request.user)
    prod.activo = False
    prod.save(update_fields=['activo'])
    return redirect('configuracion')


@require_http_methods(["POST"])
@login_required
def vincular_producto_reemplazo(request):
    from configuracion.services.productos import vincular_producto

    anterior = get_object_or_404(Producto, pk=request.POST.get("anterior_id"), user=request.user)
    canonico = get_object_or_404(Producto, pk=request.POST.get("canonico_id"), user=request.user)
    try:
        vincular_producto(request.user, anterior, canonico)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{anterior.nombre} quedó vinculado a {canonico.nombre}.")
    return redirect('configuracion')
