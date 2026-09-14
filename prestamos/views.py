from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from datetime import date, timedelta
from decimal import Decimal
from .models import Prestamo
from configuracion.models import Producto
from patrimonio.models import Activo

@login_required
def prestamos_index(request):
    user = request.user
    prestamos = Prestamo.objects.filter(user=user).order_by('-activo', '-created_at')
    try:
        tarjetas = Producto.objects.filter(user=user, tipo='TDC', activo=True)
    except Exception:
        tarjetas = []
    
    total_personal = prestamos.filter(tipo='PERSONAL', activo=True).aggregate(t=Sum('monto_total'))['t'] or 0
    total_terceros = prestamos.filter(tipo='TERCEROS', activo=True).aggregate(t=Sum('monto_total'))['t'] or 0
    costo_personal = sum(
        (p.monto_mensual for p in prestamos.filter(tipo='PERSONAL', activo=True)),
        Decimal('0'),
    )
    beneficio_terceros = sum(
        (p.monto_mensual for p in prestamos.filter(tipo='TERCEROS', activo=True)),
        Decimal('0'),
    )
    
    # Graphic data preparation
    nombres_personal = [p.nombre for p in prestamos.filter(tipo='PERSONAL', activo=True)]
    montos_personal = [float(p.monto_total) for p in prestamos.filter(tipo='PERSONAL', activo=True)]
    
    nombres_terc = [p.nombre for p in prestamos.filter(tipo='TERCEROS', activo=True)]
    montos_terc = [float(p.monto_total) for p in prestamos.filter(tipo='TERCEROS', activo=True)]

    today = date.today()
    last_month_date = today.replace(day=1) - timedelta(days=1)

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
        'default_year': last_month_date.year,
        'default_month': last_month_date.month,
        'current_year': today.year,
        'current_month': today.month,
    }
    return render(request, 'prestamos/index.html', context)

@login_required
def crear_prestamo(request):
    if request.method == 'POST':
        tarjeta_id = request.POST.get('tarjeta_asociada')
        if not tarjeta_id:
            tarjeta_id = None
        tipo = request.POST.get('tipo', 'PERSONAL')
        nombre = request.POST.get('nombre')
        monto_total = request.POST.get('monto_total', 0)
        
        prestamo = Prestamo.objects.create(
            user=request.user,
            nombre=nombre,
            tipo=tipo,
            monto_total=monto_total,
            costo_mensual_porcentaje=request.POST.get('costo_mensual_porcentaje', 0),
            tarjeta_asociada_id=tarjeta_id,
            notas=request.POST.get('notas', ''),
            activo=True
        )
        
        # Un préstamo a terceros es un activo; el movimiento asociado conserva la salida de caja.
        if tipo == 'TERCEROS':
            year = request.POST.get('year')
            month = request.POST.get('month')
            _sync_terceros_prestamo(request.user, prestamo, year=year, month=month)
        
    return redirect('prestamos_index')

@login_required
def editar_prestamo(request, pk):
    p = get_object_or_404(Prestamo, pk=pk, user=request.user)
    if request.method == 'POST':
        old_tipo = p.tipo
        old_nombre = p.nombre
        
        p.nombre = request.POST.get('nombre', p.nombre)
        p.tipo = request.POST.get('tipo', p.tipo)
        p.monto_total = request.POST.get('monto_total', p.monto_total)
        p.costo_mensual_porcentaje = request.POST.get('costo_mensual_porcentaje', p.costo_mensual_porcentaje)
        tarjeta_id = request.POST.get('tarjeta_asociada')
        p.tarjeta_asociada_id = tarjeta_id if tarjeta_id else None
        p.notas = request.POST.get('notas', p.notas)
        p.activo = request.POST.get('activo') == 'on'
        p.save()
        
        # Sync linked Activo if TERCEROS.
        if p.tipo == 'TERCEROS':
            # Update Activo
            Activo.objects.filter(
                user=request.user, 
                nombre=f"Bicicleta - {old_nombre}", 
                tipo='PRESTAMO_DADO'
            ).update(
                nombre=f"Bicicleta - {p.nombre}",
                monto_clp=p.monto_total,
                activo=p.activo
            )
            
            _sync_terceros_prestamo(request.user, p)
            
        elif old_tipo == 'TERCEROS' and p.tipo != 'TERCEROS':
            # If changed from TERCEROS to PERSONAL, deactivate the linked asset.
            Activo.objects.filter(
                user=request.user, 
                nombre=f"Bicicleta - {old_nombre}", 
                tipo='PRESTAMO_DADO'
            ).update(activo=False)
            
        
    return redirect('prestamos_index')

@login_required
def borrar_prestamo(request, pk):
    p = get_object_or_404(Prestamo, pk=pk, user=request.user)
    if request.method == 'POST':
        # Clean up linked records if TERCEROS
        if p.tipo == 'TERCEROS':
            # Delete linked Activo
            Activo.objects.filter(
                user=request.user, 
                nombre=f"Bicicleta - {p.nombre}", 
                tipo='PRESTAMO_DADO'
            ).delete()
        p.delete()
    return redirect('prestamos_index')


def _sync_terceros_prestamo(user, prestamo, year=None, month=None):
    """Create or update the recoverable asset for a loan to a third party."""
    from decimal import Decimal
    
    monto_dec = Decimal(str(prestamo.monto_total))
    Activo.objects.update_or_create(
        user=user,
        nombre=f"Bicicleta - {prestamo.nombre}",
        defaults={
            'tipo': 'PRESTAMO_DADO',
            'horizonte_temporal': 'CORTO_PLAZO',
            'es_liquido': False,
            'monto_clp': monto_dec,
            'monto_usd': Decimal('0'),
            'activo': True,
            'notas': f"Auto-generado desde Bicicleta: {prestamo.nombre}",
        }
    )
