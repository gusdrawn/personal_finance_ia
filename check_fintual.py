import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'finanzas_project.settings')
django.setup()

from patrimonio.models import Activo
from django.contrib.auth.models import User
from django.db.models import Sum

username = 'demo_user'
try:
    user = User.objects.get(username=username)
    print(f"DEBUG - Checking data for user: {username}")
    
    inversiones = Inversion.objects.filter(user=user, activo=True).order_by('tipo', 'nombre')
    print(f"Total Active Investments: {inversiones.count()}")
    
    for i in inversiones:
        print(f"  - [{i.tipo}] {i.nombre}: CLP {i.monto_clp:,.0f} (ID: {i.id})")
        
    fintual = inversiones.filter(nombre__icontains='Fintual').first()
    if fintual:
        print(f"\nFINTUAL RECORD: {fintual.monto_clp:,.0f}")
    
    afp = inversiones.filter(nombre__icontains='AFP').first()
    if afp:
        print(f"AFP RECORD: {afp.monto_clp:,.0f}")
        
except User.DoesNotExist:
    print(f"User {username} not found.")
