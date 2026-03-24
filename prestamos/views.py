from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from .models import Prestamo
from configuracion.models import Producto

@login_required
def prestamos_index(request):
    user = request.user
    prestamos = Prestamo.objects.filter(user=user).order_by('-activo', '-created_at')
    try:
        tarjetas = Producto.objects.filter(tipo='TDC')
    except Exception:
        tarjetas = []
    
    total_personal = prestamos.filter(tipo='PERSONAL', activo=True).aggregate(t=Sum('monto_total'))['t'] or 0
    total_terceros = prestamos.filter(tipo='TERCEROS', activo=True).aggregate(t=Sum('monto_total'))['t'] or 0
    costo_personal = sum(p.monto_total * (p.costo_mensual_porcentaje/100) for p in prestamos.filter(tipo='PERSONAL', activo=True))
    beneficio_terceros = sum(p.monto_total * (p.costo_mensual_porcentaje/100) for p in prestamos.filter(tipo='TERCEROS', activo=True))
    
    # Graphic data preparation
    nombres_personal = [p.nombre for p in prestamos.filter(tipo='PERSONAL', activo=True)]
    montos_personal = [float(p.monto_total) for p in prestamos.filter(tipo='PERSONAL', activo=True)]
    
    nombres_terc = [p.nombre for p in prestamos.filter(tipo='TERCEROS', activo=True)]
    montos_terc = [float(p.monto_total) for p in prestamos.filter(tipo='TERCEROS', activo=True)]

    context = {
        'prestamos': prestamos,
        'tarjetas': tarjetas,
        'total_personal': total_personal,
        'total_terceros': total_terceros,
        'costo_personal': costo_personal,
        'beneficio_terceros': beneficio_terceros,
        'nombres_personal': nombres_personal,
        'montos_personal': montos_personal,
        'nombres_terc': nombres_terc,
        'montos_terc': montos_terc,
    }
    return render(request, 'prestamos/index.html', context)

@login_required
def crear_prestamo(request):
    if request.method == 'POST':
        tarjeta_id = request.POST.get('tarjeta_asociada')
        if not tarjeta_id:
            tarjeta_id = None
        Prestamo.objects.create(
            user=request.user,
            nombre=request.POST.get('nombre'),
            tipo=request.POST.get('tipo', 'PERSONAL'),
            monto_total=request.POST.get('monto_total', 0),
            costo_mensual_porcentaje=request.POST.get('costo_mensual_porcentaje', 0),
            tarjeta_asociada_id=tarjeta_id,
            notas=request.POST.get('notas', ''),
            activo=True
        )
    return redirect('prestamos_index')

@login_required
def editar_prestamo(request, pk):
    p = get_object_or_404(Prestamo, pk=pk, user=request.user)
    if request.method == 'POST':
        p.nombre = request.POST.get('nombre', p.nombre)
        p.tipo = request.POST.get('tipo', p.tipo)
        p.monto_total = request.POST.get('monto_total', p.monto_total)
        p.costo_mensual_porcentaje = request.POST.get('costo_mensual_porcentaje', p.costo_mensual_porcentaje)
        tarjeta_id = request.POST.get('tarjeta_asociada')
        p.tarjeta_asociada_id = tarjeta_id if tarjeta_id else None
        p.notas = request.POST.get('notas', p.notas)
        p.activo = request.POST.get('activo') == 'on'
        p.save()
    return redirect('prestamos_index')

@login_required
def borrar_prestamo(request, pk):
    p = get_object_or_404(Prestamo, pk=pk, user=request.user)
    if request.method == 'POST':
        p.delete()
    return redirect('prestamos_index')
