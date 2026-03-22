import requests
from datetime import date
from decimal import Decimal
from .models import TipoCambio

def fetch_mindicador_rates():
    """Fetch current UF and USD rates from mindicador.cl"""
    try:
        response = requests.get('https://mindicador.cl/api', timeout=10)
        data = response.json()
        
        uf_val = Decimal(str(data['uf']['valor']))
        dolar_val = Decimal(str(data['dolar']['valor']))
        
        # Update or create for today
        tc, created = TipoCambio.objects.update_or_create(
            fecha=date.today(),
            fuente='mindicador.cl',
            defaults={
                'uf': uf_val,
                'dolar': dolar_val
            }
        )
        return tc, True
    except Exception as e:
        print(f"Error fetching rates: {e}")
        return None, False
