from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from .utils import fetch_mindicador_rates
from datetime import date
from decimal import Decimal
from patrimonio.models import Activo, Pasivo, SnapshotPatrimonio
from departamentos.models import Departamento
from patrimonio.models import HistorialActivo
from patrimonio.services.valoracion import resumen_patrimonio
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

        resumen = resumen_patrimonio(user, fecha=today)
        tipo_cambio = resumen["tipo_cambio"]
        valor_uf = tipo_cambio.uf if tipo_cambio else None
        valor_dolar = tipo_cambio.dolar if tipo_cambio else None

        # 1. Preserve the exact rate and converted value used for this snapshot.
        for act in resumen["activos"]:
            valor = resumen["valores_activos"][act.pk]
            HistorialActivo.objects.update_or_create(
                activo=act,
                fecha=today,
                defaults={
                    'monto_clp': valor.clp,
                    'monto_usd': valor.usd,
                    'valor_uf': valor_uf,
                    'valor_dolar': valor_dolar,
                }
            )

        SnapshotPatrimonio.objects.update_or_create(
            user=user,
            fecha=today,
            defaults={
                'activos_total_clp': resumen["activos_total_clp"],
                'activos_total_usd': resumen["activos_total_usd"],
                'pasivos_total_clp': resumen["pasivos_total_clp"],
                'pasivos_total_usd': resumen["pasivos_total_usd"],
                'patrimonio_neto_clp': resumen["patrimonio_neto_clp"],
                'patrimonio_neto_usd': resumen["patrimonio_neto_usd"],
                'activos_liquidos_clp': resumen["activos_liquidos_clp"],
                'valor_dolar': valor_dolar,
                'valor_uf': valor_uf,
            }
        )
    return redirect('configuracion')
