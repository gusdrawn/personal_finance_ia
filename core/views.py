from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.db.models import Sum, Q, F
from django.urls import reverse
from datetime import date, timedelta
from decimal import Decimal

from gastos.models import RegistroMensual, GastoProgramado, CategoriaIngreso
from patrimonio.models import Activo, Pasivo, SnapshotPatrimonio
from departamentos.models import Departamento, Estacionamiento, Arrendatario
from inversiones.models import Inversion
from configuracion.models import TipoCambio, Banco, Producto


@login_required
def dashboard(request):
    """Dashboard with key metrics and charts"""
    user = request.user
    
    # Get latest type changes
    try:
        tipo_cambio = TipoCambio.objects.filter(fuente='mindicador.cl').latest('fecha')
    except TipoCambio.DoesNotExist:
        tipo_cambio = None
    
    # Calculate patrimony
    activos_base = Activo.objects.filter(user=user).exclude(tipo__in=['INVERSION', 'DEPARTAMENTO'])
    total_activos_base = sum(a.monto_clp for a in activos_base) if activos_base.exists() else 0
    
    pasivos = Pasivo.objects.filter(user=user)
    total_pasivos = sum(p.monto_clp for p in pasivos) if pasivos.exists() else 0
    
    total_departamentos = 0
    if tipo_cambio:
        departamentos_todos = Departamento.objects.filter(user=user)
        total_departamentos = sum(d.valor_actual_uf * tipo_cambio.uf for d in departamentos_todos)
        
    inversiones_todas = Inversion.objects.filter(user=user, activo=True)
    total_inversiones = sum(i.monto_clp for i in inversiones_todas)

    total_activos = total_activos_base + total_departamentos + total_inversiones
    patrimonio_neto = total_activos - total_pasivos
    
    # Calculate liquidity
    activos_liquidos = sum(a.monto_clp for a in activos_base.filter(tipo_liquidez='LIQUIDO'))
    liquidez_inversiones = sum(i.monto_clp for i in inversiones_todas if i.tipo in ['CRIPTO', 'ACCIONES', 'FONDO_MUTUO', 'BROKERAGE'])
    total_liquidos = activos_liquidos + liquidez_inversiones
    liquidez_pct = (total_liquidos / total_activos * 100) if total_activos > 0 else 0
    
    # Current month metrics
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
    
    # Deduct TERCEROS loans from CC expenses
    try:
        from prestamos.models import Prestamo
        prestamos_terceros_activos = Prestamo.objects.filter(user=user, tipo='TERCEROS', activo=True).aggregate(t=Sum('monto_total'))['t'] or 0
        if tarjetas >= prestamos_terceros_activos:
            tarjetas -= prestamos_terceros_activos
            total_gastos -= prestamos_terceros_activos
        else:
            total_gastos -= tarjetas
            tarjetas = 0
    except Exception:
        pass
        
    otros = total_gastos - (gastos_fijos + suscripciones + tarjetas)
    
    # Get departments and investments
    departamentos = Departamento.objects.filter(user=user)[:3]
    inversiones = Inversion.objects.filter(user=user)[:4]
    
    context = {
        'patrimonio_neto': patrimonio_neto,
        'total_activos': total_activos,
        'total_pasivos': total_pasivos,
        'liquidez_pct': liquidez_pct,
        'ingresos_mes': ingresos,
        'gastos_mes': total_gastos,
        'gastos_fijos': gastos_fijos,
        'suscripciones': suscripciones,
        'tarjetas': tarjetas,
        'otros': otros,
        'tipo_cambio': tipo_cambio,
        'departamentos': departamentos,
        'inversiones': inversiones,
        'target_year': target_year,
        'target_month': target_month,
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
    
    context = {
        'current_year': last_month_date.year,
        'current_month': last_month_date.month,
        'years': years,
        'categorias': categorias,
        'gastos_programados': gastos_programados,
        'total_ahorro_anual': total_ahorro_programado,
        'total_ahorro_trimestral': Decimal('0'),
    }
    
    return render(request, 'gastos_table.html', context)


@login_required
def patrimonio(request):
    """Patrimony/Net worth view"""
    user = request.user
    
    activos_base = Activo.objects.filter(user=user).exclude(tipo__in=['INVERSION', 'DEPARTAMENTO'])
    pasivos = Pasivo.objects.filter(user=user)
    departamentos = Departamento.objects.filter(user=user)
    inversiones = Inversion.objects.filter(user=user, activo=True)
    
    try:
        tipo_cambio = TipoCambio.objects.filter(fuente='mindicador.cl').latest('fecha')
        valor_uf = tipo_cambio.uf
    except TipoCambio.DoesNotExist:
        valor_uf = Decimal(0)
    
    activos_list = []
    total_activos_base = Decimal(0)
    for a in activos_base:
        total_activos_base += a.monto_clp
        activos_list.append({
            'id': a.id,
            'nombre': a.nombre,
            'tipo': a.tipo,
            'tipo_display': a.get_tipo_display(),
            'tipo_liquidez': a.tipo_liquidez,
            'liquidez_display': a.get_tipo_liquidez_display(),
            'es_liquido': a.tipo_liquidez == 'LIQUIDO',
            'monto_clp': a.monto_clp,
            'monto_usd': a.monto_usd,
            'es_manual': True,
        })
        
    total_departamentos = Decimal(0)
    for d in departamentos:
        monto = d.valor_actual_uf * valor_uf
        total_departamentos += monto
        activos_list.append({
            'nombre': f"Departamento {d.codigo}",
            'tipo_display': 'Bienes Raíces',
            'liquidez_display': 'No Líquido',
            'es_liquido': False,
            'monto_clp': monto,
            'monto_usd': 0,
        })
        
    total_inversiones = Decimal(0)
    for i in inversiones:
        total_inversiones += i.monto_clp
        activos_list.append({
            'id': i.id,
            'nombre': i.nombre,
            'tipo_display': 'Inversión: ' + i.get_tipo_display(),
            'liquidez_display': 'Líquido' if i.tipo in ['CRIPTO', 'ACCIONES'] else 'No Líquido',
            'es_liquido': i.tipo in ['CRIPTO', 'ACCIONES'],
            'monto_clp': i.monto_clp,
            'monto_usd': i.monto_usd,
            'es_manual': False,
            'es_inversion': True
        })
        
    activos_list.sort(key=lambda x: x['monto_clp'], reverse=True)
    
    total_activos = total_activos_base + total_departamentos + total_inversiones
    total_pasivos = sum(p.monto_clp for p in pasivos)
    patrimonio_neto = total_activos - total_pasivos
    
    snapshots = SnapshotPatrimonio.objects.filter(user=user).order_by('-fecha')[:12]
    
    context = {
        'total_activos': total_activos,
        'total_pasivos': total_pasivos,
        'patrimonio_neto': patrimonio_neto,
        'activos_count': len(activos_list),
        'pasivos_count': pasivos.count(),
        'activos': activos_list,
        'pasivos': pasivos,
        'snapshots': snapshots,
        'tipo_cambio': tipo_cambio,
        'alert_msg': "Los activos de 'Bienes Raíces' se editan desde la pestaña de Departamentos, y las 'Inversiones' desde la pestaña de Inversiones."
    }
    
    return render(request, 'patrimonio.html', context)


@login_required
def departamentos(request):
    """Departments/Properties view"""
    user = request.user
    tipo_cambio = TipoCambio.objects.filter(fuente='mindicador.cl').latest('fecha')
    
    departamentos = Departamento.objects.filter(user=user).prefetch_related(
        'arrendatario', 'credito_hipotecario'
    )
    
    total_arriendos = sum(
        d.arrendatario.monto_arriendo_clp 
        for d in departamentos if d.arrendatario
    )
    promedio_arriendo = total_arriendos / departamentos.count() if departamentos.count() > 0 else 0
    
    total_valor_actual = sum(
        d.valor_actual_uf * tipo_cambio.uf 
        for d in departamentos
    )
    
    context = {
        'departamentos': departamentos,
        'total_arriendos': total_arriendos,
        'promedio_arriendo': promedio_arriendo,
        'total_valor_actual': total_valor_actual,
        'tipo_cambio': tipo_cambio.uf,
    }
    
    return render(request, 'departamentos.html', context)


@login_required
def inversiones(request):
    """Investments view"""
    user = request.user
    
    inversiones_qs = Inversion.objects.filter(user=user, activo=True).order_by('tipo', 'nombre')
    total_invertido = sum(i.monto_clp for i in inversiones_qs)
    
    # Calculate portfolio percentages on the fly
    inversiones = []
    for inv in inversiones_qs:
        inv.porcentaje_cartera = (inv.monto_clp / total_invertido * 100) if total_invertido > 0 else 0
        inversiones.append(inv)
        
    # Get portfolio history (sum of all active investments historical records)
    from django.db.models.functions import TruncMonth
    from django.db.models import Sum
    from inversiones.models import HistorialInversion
    
    historico_qs = HistorialInversion.objects.filter(
        inversion__user=user,
        inversion__activo=True,
        fecha__gte=date.today() - timedelta(days=365)
    ).annotate(month=TruncMonth('fecha')).values('month').annotate(total=Sum('monto_clp')).order_by('month')
    
    historico = {
        'labels': [h['month'].strftime('%b %Y') for h in historico_qs],
        'values': [float(h['total']) for h in historico_qs]
    }
    
    context = {
        'inversiones': inversiones,
        'total_invertido': total_invertido,
        'historico': historico if historico['labels'] else None,
        'diversificacion_pct': (len(inversiones) / 10.0 * 100) if len(inversiones) < 10 else 100, # Simple index
    }
    
    return render(request, 'inversiones.html', context)


@login_required
def configuracion(request):
    """Configuration/Settings view"""
    user = request.user
    tipo_cambio = TipoCambio.objects.filter(fuente='mindicador.cl').latest('fecha')
    bancos = Banco.objects.all()
    categorias = CategoriaIngreso.objects.filter(user=user)
    
    context = {
        'tipo_cambio': tipo_cambio,
        'bancos': bancos,
        'categorias': categorias,
    }
    
    return render(request, 'configuracion.html', context)

@require_http_methods(["POST"])
@login_required
def crear_activo(request):
    monto_clp = request.POST.get('monto_clp')
    if not monto_clp: monto_clp = 0
    monto_usd = request.POST.get('monto_usd')
    if not monto_usd: monto_usd = 0
    
    Activo.objects.create(
        user=request.user,
        nombre=request.POST.get('nombre', 'Nuevo Activo'),
        tipo=request.POST.get('tipo', 'OTRO'),
        tipo_liquidez=request.POST.get('tipo_liquidez', 'LIQUIDO'),
        monto_clp=monto_clp,
        monto_usd=monto_usd
    )
    return redirect('patrimonio')

@require_http_methods(["POST"])
@login_required
def crear_pasivo(request):
    monto_clp = request.POST.get('monto_clp')
    if not monto_clp: monto_clp = 0
    monto_usd = request.POST.get('monto_usd')
    if not monto_usd: monto_usd = 0
    
    Pasivo.objects.create(
        user=request.user,
        nombre=request.POST.get('nombre', 'Nueva Deuda'),
        tipo=request.POST.get('tipo', 'OTRO'),
        monto_clp=monto_clp,
        monto_usd=monto_usd
    )
    return redirect('patrimonio')

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

@require_http_methods(["POST"])
@login_required
def editar_activo(request, pk):
    activo = Activo.objects.get(pk=pk, user=request.user)
    monto_clp = request.POST.get('monto_clp')
    if not monto_clp: monto_clp = 0
    monto_usd = request.POST.get('monto_usd')
    if not monto_usd: monto_usd = 0
    
    activo.nombre = request.POST.get('nombre')
    activo.tipo = request.POST.get('tipo')
    activo.tipo_liquidez = request.POST.get('tipo_liquidez')
    activo.monto_clp = monto_clp
    activo.monto_usd = monto_usd
    activo.save()
    return redirect('patrimonio')

@require_http_methods(["POST"])
@login_required
def borrar_activo(request, pk):
    activo = Activo.objects.get(pk=pk, user=request.user)
    activo.delete()
    return redirect('patrimonio')

@require_http_methods(["POST"])
@login_required
def editar_pasivo(request, pk):
    pasivo = Pasivo.objects.get(pk=pk, user=request.user)
    monto_clp = request.POST.get('monto_clp')
    if not monto_clp: monto_clp = 0
    monto_usd = request.POST.get('monto_usd')
    if not monto_usd: monto_usd = 0
    
    pasivo.nombre = request.POST.get('nombre')
    pasivo.tipo = request.POST.get('tipo')
    pasivo.monto_clp = monto_clp
    pasivo.monto_usd = monto_usd
    pasivo.save()
    return redirect('patrimonio')

@require_http_methods(["POST"])
@login_required
def borrar_pasivo(request, pk):
    pasivo = Pasivo.objects.get(pk=pk, user=request.user)
    pasivo.delete()
    return redirect('patrimonio')

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
    return redirect('departamentos')

@require_http_methods(["POST"])
@login_required
def borrar_departamento(request, pk):
    depto = Departamento.objects.get(pk=pk, user=request.user)
    depto.delete()
    return redirect('departamentos')

@require_http_methods(["POST"])
@login_required
def editar_inversion(request, pk):
    inv = Inversion.objects.get(pk=pk, user=request.user)
    inv.nombre = request.POST.get('nombre')
    inv.tipo = request.POST.get('tipo')
    inv.monto_clp = request.POST.get('monto_clp', 0)
    inv.monto_usd = request.POST.get('monto_usd', 0)
    inv.activo = request.POST.get('activo') == 'on'
    inv.save()
    return redirect('inversiones')

@require_http_methods(["POST"])
@login_required
def borrar_inversion(request, pk):
    inv = Inversion.objects.get(pk=pk, user=request.user)
    inv.delete()
    return redirect('inversiones')

@login_required
def bulk_gastos(request):
    """Bulk update/create monthly expenses for a given month"""
    user = request.user
    if request.method == "POST":
        year = int(request.POST.get('year'))
        month = int(request.POST.get('month'))
        
        # Categories to process
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

    # Default to last month
    today = date.today()
    last_month_date = today.replace(day=1) - timedelta(days=1)
    
    categorias = CategoriaIngreso.objects.filter(
        Q(user=user) & (Q(banco_defecto__isnull=True) | Q(banco_defecto__mostrar_en_carga_masiva=True))
    ).select_related('banco_defecto')
    
    # Group categories for the UI
    grouped_categorias = {}
    for cat in categorias:
        tipo = cat.get_tipo_display()
        if tipo not in grouped_categorias:
            grouped_categorias[tipo] = []
        grouped_categorias[tipo].append(cat)
        
    # Sort with "Ingreso" first
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

@require_http_methods(["POST"])
@login_required
def crear_categoria(request):
    """Simple view to create a category"""
    nombre = request.POST.get('nombre')
    tipo = request.POST.get('tipo', 'GASTO')
    contabilizar = request.POST.get('contabilizar') == 'on'
    moneda_defecto = request.POST.get('moneda_defecto', 'CLP')
    dia_cobro = request.POST.get('dia_cobro')
    if dia_cobro and dia_cobro.isdigit():
        dia_cobro = int(dia_cobro)
    else:
        dia_cobro = None
    
    if nombre:
        CategoriaIngreso.objects.create(
            user=request.user,
            nombre=nombre,
            tipo=tipo,
            contabilizar=contabilizar,
            moneda_defecto=moneda_defecto,
            dia_cobro=dia_cobro,
            activo=True
        )
    # Redirect back to previous page
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
        
    cat.save()
    next_url = request.POST.get('next', 'configuracion')
    return redirect(next_url)

@require_http_methods(["POST"])
@login_required
def borrar_categoria(request, pk):
    cat = CategoriaIngreso.objects.get(pk=pk, user=request.user)
    cat.delete()
    next_url = request.POST.get('next', 'configuracion')
    return redirect(next_url)

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

@login_required
def calendario(request):
    from gastos.models import CategoriaIngreso, GastoProgramado
    from departamentos.models import Departamento
    import calendar
    from datetime import date
    
    # Get current or requested year/month
    try:
        year = int(request.GET.get('year', date.today().year))
        month = int(request.GET.get('month', date.today().month))
    except (ValueError, TypeError):
        year = date.today().year
        month = date.today().month

    # Grid logic
    cal = calendar.Calendar(firstweekday=6) # Sunday start
    month_days = cal.monthdayscalendar(year, month)
    month_name = calendar.month_name[month].capitalize()

    # Data for the calendar
    categorias = CategoriaIngreso.objects.filter(user=request.user, dia_cobro__isnull=False)
    departamentos = request.user.departamentos.filter(fecha_ultima_cuota__gte=date(year, month, 1))
    
    gastos_programados = GastoProgramado.objects.filter(user=request.user, activo=True)
    
    # Find which GastosProgramados fall in this month's grid
    # We do this logic in Python for simplicity, though could be done in DB depending on DB.
    # To show them on the right day in the grid, we match `dia_cobro`.
    grid_gastos = []
    current_date = date(year, month, 1)
    
    for g in gastos_programados:
        # Calculate month difference
        if g.fecha_inicio <= current_date:
            month_diff = (year - g.fecha_inicio.year) * 12 + (month - g.fecha_inicio.month)
            show = False
            if g.frecuencia == 'MENSUAL': show = True
            elif g.frecuencia == 'BIMESTRAL' and month_diff % 2 == 0: show = True
            elif g.frecuencia == 'TRIMESTRAL' and month_diff % 3 == 0: show = True
            elif g.frecuencia == 'SEMESTRAL' and month_diff % 6 == 0: show = True
            elif g.frecuencia == 'ANUAL' and month_diff % 12 == 0: show = True
            
            if show:
                # Add a property dynamically to render it easily
                g.dia_cobro = g.fecha_inicio.day
                grid_gastos.append(g)

    # Navigation
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
        'departamentos': departamentos,
        'gastos_programados': gastos_programados,
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
        frecuencia=request.POST.get('frecuencia')
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
    gasto.save()
    return redirect('calendario')

@require_http_methods(["POST"])
@login_required
def borrar_gasto_programado(request, pk):
    from gastos.models import GastoProgramado
    gasto = GastoProgramado.objects.get(pk=pk, user=request.user)
    gasto.delete()
    return redirect('calendario')


@require_http_methods(["POST"])
@login_required
def borrar_banco(request, pk):
    banco = Banco.objects.get(pk=pk)
    banco.delete()
    return redirect('configuracion')

@require_http_methods(["POST"])
@login_required
def crear_producto(request):
    Producto.objects.create(
        banco_id=request.POST.get('banco_id'),
        nombre=request.POST.get('nombre'),
        tipo=request.POST.get('tipo', 'TDC'),
        activo=True
    )
    return redirect('configuracion')

@require_http_methods(["POST"])
@login_required
def editar_producto(request, pk):
    prod = Producto.objects.get(pk=pk)
    prod.banco_id = request.POST.get('banco_id', prod.banco_id)
    prod.nombre = request.POST.get('nombre', prod.nombre)
    prod.tipo = request.POST.get('tipo', prod.tipo)
    prod.save()
    return redirect('configuracion')

@require_http_methods(["POST"])
@login_required
def borrar_producto(request, pk):
    prod = Producto.objects.get(pk=pk)
    prod.delete()
    return redirect('configuracion')


