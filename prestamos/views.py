from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.contrib import messages
from django.db import transaction
from django.utils.dateparse import parse_date
from django.utils import timezone
from datetime import date, timedelta
from decimal import Decimal
from .models import Prestamo, HistorialPrestamo
from configuracion.models import Producto
from patrimonio.models import Activo


def _fecha_y_monto(request, monto_key="monto_total"):
    texto_fecha = request.POST.get("fecha_cambio", "")
    fecha = parse_date(texto_fecha) if texto_fecha else timezone.localdate()
    try:
        monto = Decimal(str(request.POST.get(monto_key, "")))
    except Exception:
        monto = Decimal("-1")
    if fecha is None or fecha > timezone.localdate() or not monto.is_finite() or monto < 0:
        raise ValueError("Indica una fecha no futura y un monto CLP válido.")
    return fecha, monto


def _registrar_estado(prestamo, fecha, monto, activo, motivo="", *, es_saldo_confirmado=True):
    HistorialPrestamo.objects.create(
        prestamo=prestamo, fecha=fecha, monto_total=monto, activo=activo,
        motivo=motivo[:255],
        es_saldo_confirmado=es_saldo_confirmado,
    )
    vigente = prestamo.historial.order_by("-fecha", "-id").first()
    prestamo.monto_total = vigente.monto_total
    prestamo.activo = vigente.activo
    prestamo.save(update_fields=["monto_total", "activo", "updated_at"])

@login_required
def prestamos_index(request):
    user = request.user
    prestamos = Prestamo.objects.filter(user=user).prefetch_related('historial').order_by('-activo', '-created_at')
    for prestamo in prestamos:
        prestamo.saldo_recuperable_actual = prestamo.saldo_recuperable()
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
        'today': today,
    }
    return render(request, 'prestamos/index.html', context)

@login_required
@transaction.atomic
def crear_prestamo(request):
    if request.method == 'POST':
        try:
            fecha_cambio, monto_total = _fecha_y_monto(request)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('prestamos_index')
        tarjeta_id = request.POST.get('tarjeta_asociada')
        if not tarjeta_id:
            tarjeta_id = None
        elif not Producto.objects.filter(pk=tarjeta_id, user=request.user).exists():
            return redirect('prestamos_index')
        tipo = request.POST.get('tipo', 'PERSONAL')
        nombre = request.POST.get('nombre')
        with transaction.atomic():
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
            _registrar_estado(prestamo, fecha_cambio, monto_total, True, request.POST.get('motivo_cambio', 'Inicio'), es_saldo_confirmado=False)
        
        # Un préstamo a terceros es un activo; el movimiento asociado conserva la salida de caja.
        if tipo == 'TERCEROS':
            year = request.POST.get('year')
            month = request.POST.get('month')
            _sync_terceros_prestamo(request.user, prestamo, year=year, month=month)
        
    return redirect('prestamos_index')

@login_required
@transaction.atomic
def editar_prestamo(request, pk):
    p = get_object_or_404(Prestamo, pk=pk, user=request.user)
    if request.method == 'POST':
        try:
            fecha_cambio, monto_total = _fecha_y_monto(request)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect('prestamos_index')
        old_tipo = p.tipo
        old_monto, old_activo = p.monto_total, p.activo
        
        p.nombre = request.POST.get('nombre', p.nombre)
        p.tipo = request.POST.get('tipo', p.tipo)
        p.costo_mensual_porcentaje = request.POST.get('costo_mensual_porcentaje', p.costo_mensual_porcentaje)
        tarjeta_id = request.POST.get('tarjeta_asociada')
        if tarjeta_id and not Producto.objects.filter(pk=tarjeta_id, user=request.user).exists():
            return redirect('prestamos_index')
        p.tarjeta_asociada_id = tarjeta_id if tarjeta_id else None
        p.notas = request.POST.get('notas', p.notas)
        nuevo_activo = request.POST.get('activo') == 'on'
        p.save()
        if monto_total != old_monto or nuevo_activo != old_activo:
            _registrar_estado(p, fecha_cambio, monto_total, nuevo_activo, request.POST.get('motivo_cambio', 'Ajuste'))
        
        # Sync linked Activo if TERCEROS.
        if p.tipo == 'TERCEROS':
            _sync_terceros_prestamo(request.user, p)
            
        elif old_tipo == 'TERCEROS' and p.tipo != 'TERCEROS':
            # If changed from TERCEROS to PERSONAL, deactivate the linked asset.
            _sync_terceros_prestamo(request.user, p)
            
        
    return redirect('prestamos_index')


@login_required
@transaction.atomic
def registrar_historial_prestamo(request, pk):
    prestamo = get_object_or_404(Prestamo, pk=pk, user=request.user)
    if request.method != 'POST':
        return redirect('prestamos_index')
    try:
        fecha, monto = _fecha_y_monto(request)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect('prestamos_index')
    _registrar_estado(
        prestamo, fecha, monto, request.POST.get('activo') == 'on',
        request.POST.get('motivo_cambio', 'Corrección histórica'),
    )
    if prestamo.tipo == 'TERCEROS':
        _sync_terceros_prestamo(request.user, prestamo)
    messages.success(request, 'Cambio de bicicleta registrado con su fecha efectiva.')
    return redirect('prestamos_index')

@login_required
@transaction.atomic
def borrar_prestamo(request, pk):
    p = get_object_or_404(Prestamo, pk=pk, user=request.user)
    if request.method == 'POST':
        p.delete()
    return redirect('prestamos_index')


def _sync_terceros_prestamo(user, prestamo, year=None, month=None):
    """Create or update the recoverable asset for a loan to a third party."""
    from prestamos.services import sincronizar_activo_prestamo

    if prestamo.user_id != user.pk:
        raise ValueError('El préstamo no pertenece al usuario.')
    return sincronizar_activo_prestamo(prestamo)
