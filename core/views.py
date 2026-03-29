from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.db.models import Sum, Q, F, Avg
from django.urls import reverse
from datetime import date, timedelta
from decimal import Decimal

from gastos.models import RegistroMensual, GastoProgramado, CategoriaIngreso
from patrimonio.models import Activo, HistorialActivo, Pasivo, SnapshotPatrimonio
from departamentos.models import Departamento, Estacionamiento, Arrendatario, CreditoHipotecario
from configuracion.models import TipoCambio, Banco, Producto


def ensure_pasivos_for_products(user):
    """
    Ensures that all relevant financial products (TDC, CREDITO_CONSUMO, CREDITO_HIPOTECARIO) 
    have a corresponding Pasivo record.
    """
    from configuracion.models import Producto
    from patrimonio.models import Pasivo
    
    productos_deuda = Producto.objects.filter(
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


def ensure_departamento_categories(user):
    """
    Ensures each department has an INGRESO category for tracking rent payments.
    """
    from departamentos.models import Departamento
    from gastos.models import CategoriaIngreso
    
    deptos = Departamento.objects.filter(user=user)
    for depto in deptos:
        cat_nombre = f"Arriendo {depto.codigo}"
        CategoriaIngreso.objects.get_or_create(
            user=user,
            nombre=cat_nombre,
            tipo='INGRESO',
            defaults={
                'mostrar_en_carga_masiva': True,
                'moneda_defecto': 'CLP',
                'contabilizar': True,
                'activo': True,
            }
        )


@login_required
def dashboard(request):
    """Dashboard with key metrics and charts"""
    user = request.user
    
    # Get latest exchange rates
    try:
        tipo_cambio = TipoCambio.objects.filter(fuente='mindicador.cl').latest('fecha')
        valor_uf = tipo_cambio.uf
    except TipoCambio.DoesNotExist:
        tipo_cambio = None
        valor_uf = Decimal(0)
    
    # Calculate patrimony from unified Activo model
    activos_all = Activo.objects.filter(user=user, activo=True).exclude(tipo='DEPARTAMENTO')
    total_activos_base = sum(a.monto_clp for a in activos_all) if activos_all.exists() else 0
    
    pasivos = Pasivo.objects.filter(user=user, activo=True)
    total_pasivos = sum(p.monto_clp for p in pasivos) if pasivos.exists() else 0
    
    total_departamentos = 0
    if tipo_cambio:
        departamentos_todos = Departamento.objects.filter(user=user)
        total_departamentos = sum(d.valor_actual_uf * tipo_cambio.uf for d in departamentos_todos)
        
    total_activos = total_activos_base + total_departamentos
    patrimonio_neto = total_activos - total_pasivos
    
    # Calculate liquidity
    total_liquidos = sum(a.monto_clp for a in activos_all.filter(es_liquido=True))
    total_no_liquidos = total_activos - total_liquidos
    liquidez_pct = (total_liquidos / total_activos * 100) if total_activos > 0 else 0
    
    # Current month metrics - default to last month with data
    selected_year = request.GET.get('year')
    selected_month = request.GET.get('month')
    
    if selected_year and selected_month:
        target_year = int(selected_year)
        target_month = int(selected_month)
    else:
        latest_registro = RegistroMensual.objects.filter(user=user).order_by('-year', '-mes').first()
        if latest_registro:
            target_year = latest_registro.year
            target_month = latest_registro.mes
        else:
            today = date.today()
            target_year = today.year
            target_month = today.month
        
    ingresos = RegistroMensual.objects.filter(
        user=user, year=target_year, mes=target_month, tipo='INGRESO',
        categoria__contabilizar=True
    ).aggregate(total=Sum('monto'))['total'] or 0
    
    gastos_qs = RegistroMensual.objects.filter(
        user=user, year=target_year, mes=target_month,
        categoria__contabilizar=True
    ).exclude(tipo='INGRESO')
    
    total_gastos = gastos_qs.aggregate(total=Sum('monto'))['total'] or 0
    
    # Breakdown for chart - Using actual category types
    gastos_fijos = gastos_qs.filter(categoria__tipo='GASTO_FIJO').aggregate(total=Sum('monto'))['total'] or 0
    suscripciones = gastos_qs.filter(categoria__tipo='SUSCRIPCION').aggregate(total=Sum('monto'))['total'] or 0
    tarjetas = gastos_qs.filter(categoria__tipo='TDC').aggregate(total=Sum('monto'))['total'] or 0
    
    otros = total_gastos - (gastos_fijos + suscripciones + tarjetas)
    
    # Historical Patrimonio (snapshots)
    snapshots = SnapshotPatrimonio.objects.filter(user=user).order_by('fecha')
    snapshot_labels = [s.fecha.strftime('%b %Y') for s in snapshots]
    snapshot_activos = [float(s.activos_total_clp) for s in snapshots]
    snapshot_pasivos = [float(s.pasivos_total_clp) for s in snapshots]
    snapshot_neto = [float(s.patrimonio_neto_clp) for s in snapshots]
    
    # Historical Income/Expense (last 6 months)
    today = date.today()
    historico_meses = []
    for i in range(5, -1, -1):
        d = today.replace(day=1) - timedelta(days=30 * i)
        y, m = d.year, d.month
        ing = RegistroMensual.objects.filter(
            user=user, year=y, mes=m, tipo='INGRESO', categoria__contabilizar=True
        ).aggregate(t=Sum('monto'))['t'] or 0
        gas = RegistroMensual.objects.filter(
            user=user, year=y, mes=m, categoria__contabilizar=True
        ).exclude(tipo='INGRESO').aggregate(t=Sum('monto'))['t'] or 0
        historico_meses.append({
            'label': f"{m:02d}/{y}",
            'ingresos': float(ing),
            'gastos': float(gas),
            'balance': float(ing - gas),
        })
    
    # Get departments and top activos for previews
    departamentos = Departamento.objects.filter(user=user)[:3]
    top_activos = Activo.objects.filter(user=user, activo=True).exclude(tipo='DEPARTAMENTO').order_by('-monto_clp')[:4]
    
    # Available months for the month selector
    available_months = RegistroMensual.objects.filter(user=user).values('year', 'mes').distinct().order_by('-year', '-mes')[:24]
    
    context = {
        'patrimonio_neto': patrimonio_neto,
        'total_activos': total_activos,
        'total_pasivos': total_pasivos,
        'total_liquidos': total_liquidos,
        'total_no_liquidos': total_no_liquidos,
        'liquidez_pct': liquidez_pct,
        'ingresos_mes': ingresos,
        'gastos_mes': total_gastos,
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
        'available_months': list(available_months),
    }
    
    return render(request, 'dashboard.html', context)


@login_required
def gastos_table(request):
    """Monthly expenses table view"""
    user = request.user
    today = date.today()
    last_month_date = today.replace(day=1) - timedelta(days=1)
    
    years = range(today.year - 2, today.year + 2)
    categorias = CategoriaIngreso.objects.filter(user=user)
    gastos_programados = GastoProgramado.objects.filter(user=user, activo=True)
    
    # Calculate total savings needed
    total_ahorro_programado = sum(g.ahorro_mensual for g in gastos_programados)
    
    # Gather categories for the bulk modal
    categorias_bulk = CategoriaIngreso.objects.filter(
        user=user, mostrar_en_carga_masiva=True
    ).select_related('banco_defecto')
    
    grouped_categorias = {}
    for cat in categorias_bulk:
        tipo = cat.get_tipo_display()
        if tipo not in grouped_categorias:
            grouped_categorias[tipo] = []
        grouped_categorias[tipo].append(cat)
        
    tipo_order = {'Ingreso': 0, 'Gasto': 1, 'Gasto Fijo': 2, 'Tarjeta de Crédito': 3}
    grouped_sorted = dict(sorted(grouped_categorias.items(), key=lambda x: tipo_order.get(x[0], 99)))
    
    context = {
        'current_year': last_month_date.year,
        'current_month': last_month_date.month,
        'default_year': last_month_date.year,
        'default_month': last_month_date.month,
        'years': years,
        'categorias': categorias,
        'grouped_categorias': grouped_sorted,
        'gastos_programados': gastos_programados,
        'total_ahorro_anual': total_ahorro_programado,
        'total_ahorro_trimestral': Decimal('0'),
    }
    
    return render(request, 'gastos_table.html', context)


@login_required
def patrimonio(request):
    """Patrimony/Net worth view - Summary page"""
    user = request.user
    
    activos_all = Activo.objects.filter(user=user, activo=True).exclude(tipo='DEPARTAMENTO')
    pasivos = Pasivo.objects.filter(user=user, activo=True)
    departamentos = Departamento.objects.filter(user=user)
    
    try:
        tipo_cambio = TipoCambio.objects.filter(fuente='mindicador.cl').latest('fecha')
        valor_uf = tipo_cambio.uf
    except TipoCambio.DoesNotExist:
        valor_uf = Decimal(0)
        tipo_cambio = None
    
    # Totals
    total_activos_base = sum(a.monto_clp for a in activos_all)
    total_departamentos = sum(d.valor_actual_uf * valor_uf for d in departamentos)
    total_activos = total_activos_base + total_departamentos
    total_pasivos = sum(p.monto_clp for p in pasivos)
    patrimonio_neto = total_activos - total_pasivos
    
    total_liquidos = sum(a.monto_clp for a in activos_all.filter(es_liquido=True))
    total_no_liquidos = total_activos - total_liquidos
    liquidez_pct = (total_liquidos / total_activos * 100) if total_activos > 0 else 0
    
    snapshots = SnapshotPatrimonio.objects.filter(user=user).order_by('-fecha')[:12]
    
    context = {
        'total_activos': total_activos,
        'total_pasivos': total_pasivos,
        'patrimonio_neto': patrimonio_neto,
        'total_liquidos': total_liquidos,
        'total_no_liquidos': total_no_liquidos,
        'liquidez_pct': liquidez_pct,
        'activos_count': activos_all.count() + departamentos.count(),
        'pasivos_count': pasivos.count(),
        'snapshots': snapshots,
        'tipo_cambio': tipo_cambio,
    }
    
    return render(request, 'patrimonio.html', context)


@login_required
def activos_view(request):
    """Unified Assets view (replaces Inversiones)"""
    user = request.user
    
    try:
        tipo_cambio = TipoCambio.objects.filter(fuente='mindicador.cl').latest('fecha')
        valor_uf = tipo_cambio.uf
    except TipoCambio.DoesNotExist:
        valor_uf = Decimal(0)
        tipo_cambio = None
    
    # All user assets (excluding departamentos which are auto-generated)
    activos_qs = Activo.objects.filter(user=user, activo=True).exclude(tipo='DEPARTAMENTO')
    
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
            'es_depto': True,
            'depto_id': d.id,
        })
    
    total_invertido = sum(a.monto_clp for a in activos_qs) + total_depto_clp
    
    # Group by horizonte_temporal
    HORIZONTE_ORDER = {'EFECTIVO': 0, 'CORTO_PLAZO': 1, 'MEDIANO_PLAZO': 2, 'LARGO_PLAZO': 3}
    HORIZONTE_LABELS = dict(Activo.HORIZONTE_CHOICES)
    
    grouped = {}
    for h_key, h_label in Activo.HORIZONTE_CHOICES:
        items = list(activos_qs.filter(horizonte_temporal=h_key))
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
    total_liquidos = sum(a.monto_clp for a in activos_qs.filter(es_liquido=True))
    total_no_liquidos = total_invertido - total_liquidos
    liquidez_pct = (total_liquidos / total_invertido * 100) if total_invertido > 0 else 0
    
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
        'activos_count': activos_qs.count() + len(depto_activos),
        'chart_data': chart_data,
        'historico': historico if historico['labels'] else None,
        'tipo_cambio': tipo_cambio,
    }
    
    return render(request, 'activos.html', context)


@login_required
def pasivos_view(request):
    """Liabilities dedicated page"""
    user = request.user
    
    # # Sync relevant products that don't have a Pasivo yet (e.g. existing TDC)
    # ensure_pasivos_for_products(user)
    
    try:
        tipo_cambio = TipoCambio.objects.filter(fuente='mindicador.cl').latest('fecha')
    except TipoCambio.DoesNotExist:
        tipo_cambio = None
    
    pasivos_all = Pasivo.objects.filter(user=user, activo=True)
    total_pasivos = sum(p.monto_clp for p in pasivos_all)
    
    # Group by tipo
    TIPO_ORDER = {'TDC': 0, 'CREDITO_HIPOTECARIO': 1, 'CREDITO_CONSUMO': 2, 'PRESTAMO': 3, 'OTRO': 4}
    TIPO_LABELS = dict(Pasivo.TIPO_PASIVO)
    
    grouped = {}
    for t_key, t_label in Pasivo.TIPO_PASIVO:
        items = list(pasivos_all.filter(tipo=t_key))
        if items:
            grouped[t_key] = {
                'label': t_label,
                'items': items,
                'total': sum(p.monto_clp for p in items),
            }
    
    # Activos for ratio
    activos_total = sum(a.monto_clp for a in Activo.objects.filter(user=user, activo=True))
    departamentos = Departamento.objects.filter(user=user)
    if tipo_cambio:
        activos_total += sum(d.valor_actual_uf * tipo_cambio.uf for d in departamentos)
    
    ratio_da = (total_pasivos / activos_total * 100) if activos_total > 0 else 0
    
    # Available productos for linking
    productos = Producto.objects.filter(activo=True).select_related('banco')
    
    context = {
        'grouped': grouped,
        'total_pasivos': total_pasivos,
        'pasivos_count': pasivos_all.count(),
        'ratio_da': ratio_da,
        'tipo_cambio': tipo_cambio,
        'productos': productos,
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
    
    # Sync categories for departments if missing
    ensure_departamento_categories(user)
    
    departamentos_qs = Departamento.objects.filter(user=user).prefetch_related(
        'arrendatario', 'credito_hipotecario'
    )
    
    total_arriendos = sum(
        d.arrendatario.monto_arriendo_clp 
        for d in departamentos_qs if hasattr(d, 'arrendatario')
    )
    promedio_arriendo = total_arriendos / departamentos_qs.count() if departamentos_qs.count() > 0 else 0
    
    total_valor_actual = sum(
        d.valor_actual_uf * valor_uf 
        for d in departamentos_qs
    )
    
    bancos = Banco.objects.all()
    
    # Calculate ROI metrics for each departamento
    deptos_con_roi = []
    for d in departamentos_qs:
        info = {
            'depto': d,
            'valor_clp': d.valor_actual_uf * valor_uf,
        }
        
        if hasattr(d, 'arrendatario'):
            renta_esperada = d.arrendatario.monto_arriendo_clp
            # Check if there's a category for this depto in RegistroMensual
            cat_nombre = f"Arriendo {d.codigo}"
            ingreso_real = RegistroMensual.objects.filter(
                user=user, categoria__nombre=cat_nombre
            ).aggregate(avg=Avg('monto'))['avg'] or Decimal(0)
            
            info['renta_esperada'] = renta_esperada
            info['ingreso_real'] = ingreso_real
            info['costo_admin'] = renta_esperada - ingreso_real if ingreso_real > 0 else Decimal(0)
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
            info['pct_recaudacion'] = 0
            info['missing_pct'] = 100
            info['dividendo_clp'] = 0
            info['cobertura_dividendo'] = 0
            info['diferencia_dividendo'] = 0
            info['utilidad_patrimonial'] = 0
            info['roi_total'] = 0
        
        deptos_con_roi.append(info)

    # Productos CREDITO_HIPOTECARIO available from Entidades Financieras
    creditos_hip_productos = Producto.objects.filter(
        tipo='CREDITO_HIPOTECARIO', activo=True
    ).select_related('banco')

    # Calculate Global Portfolio Metrics for Cards
    total_ingreso_real = sum(i['ingreso_real'] for i in deptos_con_roi)
    total_renta_pactada = sum(i['renta_esperada'] for i in deptos_con_roi)
    total_cobertura_dividendo = sum(i['diferencia_dividendo'] for i in deptos_con_roi)
    total_utilidad_patrimonial = sum(i['utilidad_patrimonial'] for i in deptos_con_roi)
    
    total_amort_uf = sum(
        d.credito_hipotecario.calcular_amortizacion_mensual()
        for d in departamentos_qs if hasattr(d, 'credito_hipotecario')
    )
    total_amort_clp = total_amort_uf * valor_uf
    
    # Average Cap Rate (weighted by current market value)
    total_value_clp = sum(i['valor_clp'] for i in deptos_con_roi)
    avg_cap_rate = float(total_ingreso_real * 12 / total_value_clp * 100) if total_value_clp > 0 else 0

    context = {
        'departamentos': departamentos_qs,
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
    }
    
    return render(request, 'departamentos.html', context)


@login_required
def configuracion(request):
    """Configuration/Settings view"""
    user = request.user
    try:
        tipo_cambio = TipoCambio.objects.filter(fuente='mindicador.cl').latest('fecha')
    except TipoCambio.DoesNotExist:
        tipo_cambio = None
    bancos = Banco.objects.all()
    categorias = CategoriaIngreso.objects.filter(user=user)
    
    context = {
        'tipo_cambio': tipo_cambio,
        'bancos': bancos,
        'categorias': categorias,
    }
    
    return render(request, 'configuracion.html', context)


# ===================== ACTIVOS CRUD =====================

@require_http_methods(["POST"])
@login_required
def crear_activo(request):
    monto_clp = request.POST.get('monto_clp') or 0
    monto_usd = request.POST.get('monto_usd') or 0
    
    Activo.objects.create(
        user=request.user,
        nombre=request.POST.get('nombre', 'Nuevo Activo'),
        tipo=request.POST.get('tipo', 'OTRO'),
        horizonte_temporal=request.POST.get('horizonte_temporal', 'EFECTIVO'),
        es_liquido=request.POST.get('es_liquido') == 'on',
        monto_clp=monto_clp,
        monto_usd=monto_usd,
        notas=request.POST.get('notas', ''),
    )
    next_url = request.POST.get('next', 'activos')
    return redirect(next_url)


@require_http_methods(["POST"])
@login_required
def editar_activo(request, pk):
    activo = Activo.objects.get(pk=pk, user=request.user)
    activo.nombre = request.POST.get('nombre', activo.nombre)
    activo.tipo = request.POST.get('tipo', activo.tipo)
    activo.horizonte_temporal = request.POST.get('horizonte_temporal', activo.horizonte_temporal)
    activo.es_liquido = request.POST.get('es_liquido') == 'on'
    activo.monto_clp = request.POST.get('monto_clp') or 0
    activo.monto_usd = request.POST.get('monto_usd') or 0
    activo.notas = request.POST.get('notas', activo.notas)
    activo.save()
    next_url = request.POST.get('next', 'activos')
    return redirect(next_url)


@require_http_methods(["POST"])
@login_required
def borrar_activo(request, pk):
    activo = Activo.objects.get(pk=pk, user=request.user)
    activo.delete()
    next_url = request.POST.get('next', 'activos')
    return redirect(next_url)


# ===================== PASIVOS CRUD =====================

@require_http_methods(["POST"])
@login_required
def crear_pasivo(request):
    monto_clp = request.POST.get('monto_clp') or 0
    monto_usd = request.POST.get('monto_usd') or 0
    
    producto_id = request.POST.get('producto_id')
    producto = Producto.objects.get(id=producto_id) if producto_id else None
    
    Pasivo.objects.create(
        user=request.user,
        nombre=request.POST.get('nombre', 'Nueva Deuda'),
        tipo=request.POST.get('tipo', 'OTRO'),
        monto_clp=monto_clp,
        monto_usd=monto_usd,
        producto=producto,
        notas=request.POST.get('notas', ''),
    )
    next_url = request.POST.get('next', 'pasivos')
    return redirect(next_url)


@require_http_methods(["POST"])
@login_required
def editar_pasivo(request, pk):
    pasivo = Pasivo.objects.get(pk=pk, user=request.user)
    pasivo.nombre = request.POST.get('nombre', pasivo.nombre)
    pasivo.tipo = request.POST.get('tipo', pasivo.tipo)
    pasivo.monto_clp = request.POST.get('monto_clp') or 0
    pasivo.monto_usd = request.POST.get('monto_usd') or 0
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
    
    # Auto-create INGRESO category for Carga Masiva
    CategoriaIngreso.objects.get_or_create(
        user=request.user,
        nombre=f"Arriendo {depto.codigo}",
        tipo='INGRESO',
        defaults={
            'mostrar_en_carga_masiva': True,
            'moneda_defecto': 'CLP',
            'contabilizar': True,
            'activo': True,
        }
    )
    
    return redirect('departamentos')


@require_http_methods(["POST"])
@login_required
def editar_departamento(request, pk):
    depto = Departamento.objects.get(pk=pk, user=request.user)
    old_codigo = depto.codigo
    
    depto.codigo = request.POST.get('codigo')
    depto.piso = request.POST.get('piso')
    depto.metros_cuadrados = request.POST.get('metros_cuadrados')
    depto.valor_compra_uf = request.POST.get('valor_compra_uf')
    depto.valor_actual_uf = request.POST.get('valor_actual_uf')
    depto.save()
    
    # Sync category name if code changed
    if old_codigo != depto.codigo:
        old_cat_nombre = f"Arriendo {old_codigo}"
        new_cat_nombre = f"Arriendo {depto.codigo}"
        CategoriaIngreso.objects.filter(
            user=request.user, nombre=old_cat_nombre, tipo='INGRESO'
        ).update(nombre=new_cat_nombre)
        
    return redirect('departamentos')


@require_http_methods(["POST"])
@login_required
def borrar_departamento(request, pk):
    depto = Departamento.objects.get(pk=pk, user=request.user)
    # Also remove associated INGRESO category
    CategoriaIngreso.objects.filter(
        user=request.user, nombre=f"Arriendo {depto.codigo}", tipo='INGRESO'
    ).delete()
    depto.delete()
    return redirect('departamentos')


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
        producto = Producto.objects.get(id=producto_id)
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
        credito.banco = Producto.objects.get(id=producto_id).banco
    
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
    """Bulk update/create monthly expenses for a given month"""
    user = request.user
    if request.method == "POST":
        year = int(request.POST.get('year'))
        month = int(request.POST.get('month'))
        
        for key, value in request.POST.items():
            if key.startswith('cat_'):
                cat_id = key.split('_')[1]
                monto = value.strip()
                
                if monto:
                    decimal_monto = Decimal(monto.replace('.', '').replace(',', '.'))
                    categoria = CategoriaIngreso.objects.get(id=cat_id, user=user)
                    
                    RegistroMensual.objects.update_or_create(
                        user=user,
                        year=year,
                        mes=month,
                        categoria=categoria,
                        defaults={
                            'monto': decimal_monto,
                            'tipo': categoria.tipo if categoria.tipo != 'GASTO_FIJO' else 'GASTO',
                            'moneda': 'CLP'
                        }
                    )
        
        return redirect(f'/dashboard/gastos/?year={year}&month={month}')

    today = date.today()
    last_month_date = today.replace(day=1) - timedelta(days=1)
    
    categorias = CategoriaIngreso.objects.filter(
        user=user, mostrar_en_carga_masiva=True
    ).select_related('banco_defecto')
    
    grouped_categorias = {}
    for cat in categorias:
        tipo = cat.get_tipo_display()
        if tipo not in grouped_categorias:
            grouped_categorias[tipo] = []
        grouped_categorias[tipo].append(cat)
        
    tipo_order = {'Ingreso': 0, 'Gasto': 1, 'Gasto Fijo': 2, 'Tarjeta de Crédito': 3}
    grouped_sorted = dict(sorted(grouped_categorias.items(), key=lambda x: tipo_order.get(x[0], 99)))
    
    context = {
        'years': range(today.year - 2, today.year + 2),
        'default_year': last_month_date.year,
        'default_month': last_month_date.month,
        'grouped_categorias': grouped_sorted,
    }
    return render(request, 'bulk_gastos_modal.html', context)


@login_required
def get_category_data(request, cat_pk):
    """Return JSON with history for a category"""
    from django.http import JsonResponse
    user = request.user
    categoria = CategoriaIngreso.objects.get(pk=cat_pk, user=user)
    history = RegistroMensual.objects.filter(
        user=user, categoria=categoria
    ).order_by('year', 'mes')[:12]
    
    data = {
        'labels': [f"{h.mes:02d}-{h.year}" for h in history],
        'values': [float(h.monto) for h in history],
        'name': categoria.nombre
    }
    return JsonResponse(data)


# ===================== CATEGORIAS CRUD =====================

@require_http_methods(["POST"])
@login_required
def crear_categoria(request):
    nombre = request.POST.get('nombre')
    tipo = request.POST.get('tipo', 'GASTO')
    contabilizar = request.POST.get('contabilizar') == 'on'
    moneda_defecto = request.POST.get('moneda_defecto', 'CLP')
    dia_cobro = request.POST.get('dia_cobro')
    if dia_cobro and dia_cobro.isdigit():
        dia_cobro = int(dia_cobro)
    else:
        dia_cobro = None
    mostrar_en_carga_masiva = request.POST.get('mostrar_en_carga_masiva') == 'on'
    
    if nombre:
        CategoriaIngreso.objects.create(
            user=request.user,
            nombre=nombre,
            tipo=tipo,
            contabilizar=contabilizar,
            moneda_defecto=moneda_defecto,
            dia_cobro=dia_cobro,
            mostrar_en_carga_masiva=mostrar_en_carga_masiva,
            activo=True
        )
    next_url = request.POST.get('next', 'configuracion')
    return redirect(next_url)


@require_http_methods(["POST"])
@login_required
def editar_categoria(request, pk):
    cat = CategoriaIngreso.objects.get(pk=pk, user=request.user)
    cat.nombre = request.POST.get('nombre', cat.nombre)
    cat.tipo = request.POST.get('tipo', cat.tipo)
    cat.contabilizar = request.POST.get('contabilizar') == 'on'
    cat.moneda_defecto = request.POST.get('moneda_defecto', 'CLP')
    
    dia_cobro = request.POST.get('dia_cobro')
    if dia_cobro and dia_cobro.isdigit():
        cat.dia_cobro = int(dia_cobro)
    else:
        cat.dia_cobro = None
        
    cat.mostrar_en_carga_masiva = request.POST.get('mostrar_en_carga_masiva') == 'on'
        
    cat.save()
    next_url = request.POST.get('next', 'configuracion')
    return redirect(next_url)


@require_http_methods(["POST"])
@login_required
def borrar_categoria(request, pk):
    cat = CategoriaIngreso.objects.get(pk=pk, user=request.user)
    if cat.producto_asociado:
        pass
    else:
        cat.delete()
    next_url = request.POST.get('next', 'configuracion')
    return redirect(next_url)


# ===================== BANCOS CRUD =====================

@require_http_methods(["POST"])
@login_required
def crear_banco(request):
    nombre = request.POST.get('nombre')
    notas = request.POST.get('notas', '')
    mostrar = request.POST.get('mostrar_en_carga_masiva') == 'on'
    if nombre:
        Banco.objects.create(
            nombre=nombre,
            notas=notas,
            mostrar_en_carga_masiva=mostrar,
            activo=True
        )
    return redirect('configuracion')


@require_http_methods(["POST"])
@login_required
def editar_banco(request, pk):
    banco = Banco.objects.get(pk=pk)
    banco.nombre = request.POST.get('nombre', banco.nombre)
    banco.notas = request.POST.get('notas', banco.notas)
    banco.mostrar_en_carga_masiva = request.POST.get('mostrar_en_carga_masiva') == 'on'
    banco.save()
    return redirect('configuracion')


@require_http_methods(["POST"])
@login_required
def borrar_banco(request, pk):
    banco = Banco.objects.get(pk=pk)
    banco.delete()
    return redirect('configuracion')


# ===================== CALENDARIO =====================

@login_required
def calendario(request):
    from gastos.models import CategoriaIngreso, GastoProgramado
    from departamentos.models import Departamento
    import calendar
    from datetime import date
    
    try:
        year = int(request.GET.get('year', date.today().year))
        month = int(request.GET.get('month', date.today().month))
    except (ValueError, TypeError):
        year = date.today().year
        month = date.today().month

    cal = calendar.Calendar(firstweekday=6)
    month_days = cal.monthdayscalendar(year, month)
    month_name = calendar.month_name[month].capitalize()

    categorias = CategoriaIngreso.objects.filter(user=request.user, dia_cobro__isnull=False)
    departamentos_cal = request.user.departamentos.filter(fecha_ultima_cuota__gte=date(year, month, 1))
    
    gastos_programados = GastoProgramado.objects.filter(user=request.user, activo=True)
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

    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    context = {
        'year': year,
        'month': month,
        'month_name': month_name,
        'month_days': month_days,
        'categorias': categorias,
        'departamentos': departamentos_cal,
        'gastos_programados': gastos_programados,
        'ahorro_mensual_total': ahorro_mensual_total,
        'grid_gastos': grid_gastos,
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
    contabilizar = request.POST.get('contabilizar') == 'on'
    dia_cobro = request.POST.get('dia_cobro')
    dia_cobro = int(dia_cobro) if dia_cobro and dia_cobro.isdigit() else None
    
    prod = Producto.objects.create(
        banco_id=banco_id,
        nombre=nombre,
        tipo=tipo,
        tiene_cupo_usd=tiene_cupo_usd,
        contabilizar=contabilizar,
        dia_cobro=dia_cobro,
        activo=True
    )
    
    from gastos.models import CategoriaIngreso
    banco = Banco.objects.get(id=banco_id)
    if tipo == 'CREDITO_CONSUMO':
        cat_tipo = 'CREDITO_CONSUMO'
    elif tipo == 'CREDITO_HIPOTECARIO':
        cat_tipo = 'CREDITO_HIPOTECARIO'
    elif tipo in ['TDC', 'LINEA_CREDITO']:
        cat_tipo = 'TDC'
    else:
        cat_tipo = 'COBRO_BANCO'
    
    CategoriaIngreso.objects.create(
        user=request.user,
        nombre=f"{banco.nombre} - {nombre} (CLP)" if tiene_cupo_usd else f"{banco.nombre} - {nombre}",
        tipo=cat_tipo,
        banco_defecto=banco,
        producto_asociado=prod,
        moneda_defecto='CLP',
        mostrar_en_carga_masiva=banco.mostrar_en_carga_masiva,
        contabilizar=contabilizar,
        dia_cobro=dia_cobro,
        activo=True
    )
    
    if tiene_cupo_usd:
        CategoriaIngreso.objects.create(
            user=request.user,
            nombre=f"{banco.nombre} - {nombre} (USD)",
            tipo=cat_tipo,
            banco_defecto=banco,
            producto_asociado=prod,
            moneda_defecto='USD',
            mostrar_en_carga_masiva=banco.mostrar_en_carga_masiva,
            contabilizar=contabilizar,
            dia_cobro=dia_cobro,
            activo=True
        )
    
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
    prod = Producto.objects.get(pk=pk)
    prod.banco_id = request.POST.get('banco_id', prod.banco_id)
    prod.nombre = request.POST.get('nombre', prod.nombre)
    prod.tipo = request.POST.get('tipo', prod.tipo)
    
    prod.tiene_cupo_usd = request.POST.get('tiene_cupo_usd') == 'on'
    prod.contabilizar = request.POST.get('contabilizar') == 'on'
    
    dia_cobro = request.POST.get('dia_cobro')
    prod.dia_cobro = int(dia_cobro) if dia_cobro and dia_cobro.isdigit() else None
    
    prod.save()
    
    # Ensure Pasivo exists if type was changed to one that requires it or if it didn't exist
    if prod.tipo in ['CREDITO_HIPOTECARIO', 'CREDITO_CONSUMO', 'TDC']:
        ensure_pasivos_for_products(request.user)
    
    from gastos.models import CategoriaIngreso
    banco = Banco.objects.get(id=prod.banco_id)
    
    if prod.tipo == 'CREDITO_CONSUMO':
        cat_tipo = 'CREDITO_CONSUMO'
    elif prod.tipo == 'CREDITO_HIPOTECARIO':
        cat_tipo = 'CREDITO_HIPOTECARIO'
    elif prod.tipo in ['TDC', 'LINEA_CREDITO']:
        cat_tipo = 'TDC'
    else:
        cat_tipo = 'COBRO_BANCO'
    
    categorias = prod.categorias_vinculadas.all()
    if categorias.exists():
        if not prod.tiene_cupo_usd:
            for cat in categorias:
                if cat.moneda_defecto == 'USD':
                    cat.delete()
                else:
                    cat.nombre = f"{banco.nombre} - {prod.nombre}"
                    cat.tipo = cat_tipo
                    cat.banco_defecto = banco
                    cat.contabilizar = prod.contabilizar
                    cat.dia_cobro = prod.dia_cobro
                    cat.save(update_fields=['nombre', 'tipo', 'banco_defecto', 'contabilizar', 'dia_cobro'])
        else:
            clp_cat = categorias.filter(moneda_defecto='CLP').first()
            if clp_cat:
                clp_cat.nombre = f"{banco.nombre} - {prod.nombre} (CLP)"
                clp_cat.tipo = cat_tipo
                clp_cat.banco_defecto = banco
                clp_cat.contabilizar = prod.contabilizar
                clp_cat.dia_cobro = prod.dia_cobro
                clp_cat.save(update_fields=['nombre', 'tipo', 'banco_defecto', 'contabilizar', 'dia_cobro'])
            else:
                CategoriaIngreso.objects.create(
                    user=request.user, nombre=f"{banco.nombre} - {prod.nombre} (CLP)", tipo=cat_tipo,
                    banco_defecto=banco, producto_asociado=prod, moneda_defecto='CLP', 
                    mostrar_en_carga_masiva=banco.mostrar_en_carga_masiva, contabilizar=prod.contabilizar, dia_cobro=prod.dia_cobro, activo=True
                )
            
            usd_cat = categorias.filter(moneda_defecto='USD').first()
            if usd_cat:
                usd_cat.nombre = f"{banco.nombre} - {prod.nombre} (USD)"
                usd_cat.tipo = cat_tipo
                usd_cat.banco_defecto = banco
                usd_cat.contabilizar = prod.contabilizar
                usd_cat.dia_cobro = prod.dia_cobro
                usd_cat.save(update_fields=['nombre', 'tipo', 'banco_defecto', 'contabilizar', 'dia_cobro'])
            else:
                CategoriaIngreso.objects.create(
                    user=request.user, nombre=f"{banco.nombre} - {prod.nombre} (USD)", tipo=cat_tipo,
                    banco_defecto=banco, producto_asociado=prod, moneda_defecto='USD', 
                    mostrar_en_carga_masiva=banco.mostrar_en_carga_masiva, contabilizar=prod.contabilizar, dia_cobro=prod.dia_cobro, activo=True
                )
        
    return redirect('configuracion')


@require_http_methods(["POST"])
@login_required
def borrar_producto(request, pk):
    prod = Producto.objects.get(pk=pk)
    prod.categorias_vinculadas.all().delete()
    prod.delete()
    return redirect('configuracion')
