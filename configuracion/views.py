from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from .utils import fetch_mindicador_rates
from datetime import date
from decimal import Decimal
from patrimonio.models import Activo, Pasivo, SnapshotPatrimonio
from departamentos.models import Departamento
from inversiones.models import Inversion, HistorialInversion
from configuracion.models import TipoCambio

@login_required
def update_rates(request):
    """View to trigger manual exchange rate update"""
    tc, success = fetch_mindicador_rates()
    # Add a message if possible (but we'd need django.contrib.messages)
    return redirect('configuracion')

@login_required
def take_snapshot(request):
    """Manually take a snapshot of the user's portfolio"""
    if request.method == "POST":
        user = request.user
        today = date.today()

        try:
            tipo_cambio = TipoCambio.objects.filter(fuente='mindicador.cl').latest('fecha')
            valor_uf = tipo_cambio.uf
            valor_dolar = tipo_cambio.dolar
        except TipoCambio.DoesNotExist:
            valor_uf = Decimal(0)
            valor_dolar = Decimal(0)

        # 1. Update Inversion History
        inversiones_activas = Inversion.objects.filter(user=user, activo=True)
        for inv in inversiones_activas:
            HistorialInversion.objects.update_or_create(
                inversion=inv,
                fecha=today,
                defaults={
                    'monto_clp': inv.monto_clp,
                    'monto_usd': inv.monto_usd,
                    'valor_uf': valor_uf,
                    'valor_dolar': valor_dolar,
                }
            )

        # 2. Calculate totals for SnapshotPatrimonio
        activos_base = Activo.objects.filter(user=user).exclude(tipo__in=['INVERSION', 'DEPARTAMENTO'])
        total_activos_base_clp = sum(a.monto_clp for a in activos_base) if activos_base.exists() else Decimal(0)
        total_activos_base_usd = sum(a.monto_usd for a in activos_base) if activos_base.exists() else Decimal(0)
        
        # liquidez
        activos_liquidos = sum(a.monto_clp for a in activos_base.filter(tipo_liquidez='LIQUIDO'))
        liquidez_inversiones = sum(i.monto_clp for i in inversiones_activas if i.tipo in ['CRIPTO', 'ACCIONES', 'FONDO_MUTUO', 'BROKERAGE'])
        total_liquidos = activos_liquidos + liquidez_inversiones

        departamentos = Departamento.objects.filter(user=user)
        total_departamentos_clp = sum(d.valor_actual_uf * valor_uf for d in departamentos)
        
        total_inversiones_clp = sum(i.monto_clp for i in inversiones_activas)
        total_inversiones_usd = sum(i.monto_usd for i in inversiones_activas)

        total_activos_clp = total_activos_base_clp + total_departamentos_clp + total_inversiones_clp
        total_activos_usd = total_activos_base_usd + total_inversiones_usd

        pasivos = Pasivo.objects.filter(user=user)
        total_pasivos_clp = sum(p.monto_clp for p in pasivos) if pasivos.exists() else Decimal(0)
        total_pasivos_usd = sum(p.monto_usd for p in pasivos) if pasivos.exists() else Decimal(0)

        patrimonio_neto_clp = total_activos_clp - total_pasivos_clp
        patrimonio_neto_usd = total_activos_usd - total_pasivos_usd

        SnapshotPatrimonio.objects.update_or_create(
            user=user,
            fecha=today,
            defaults={
                'activos_total_clp': total_activos_clp,
                'activos_total_usd': total_activos_usd,
                'pasivos_total_clp': total_pasivos_clp,
                'pasivos_total_usd': total_pasivos_usd,
                'patrimonio_neto_clp': patrimonio_neto_clp,
                'patrimonio_neto_usd': patrimonio_neto_usd,
                'activos_liquidos_clp': total_liquidos,
            }
        )
    return redirect('configuracion')
