from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from .utils import fetch_mindicador_rates

@login_required
def update_rates(request):
    """View to trigger manual exchange rate update"""
    tc, success = fetch_mindicador_rates()
    # Add a message if possible (but we'd need django.contrib.messages)
    return redirect('configuracion')
