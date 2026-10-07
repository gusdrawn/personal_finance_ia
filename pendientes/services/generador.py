from __future__ import annotations

from datetime import date, timedelta

from django.db import transaction
from django.urls import reverse

from banca.models import CredencialBanco, ImportacionEstadoCuenta, SyncLog
from configuracion.models import Producto
from pendientes.models import ReglaPendiente

from .calendario import materializar_ocurrencias


def _evidencia_producto(producto):
    fechas = list(
        ImportacionEstadoCuenta.objects.filter(producto=producto, user=producto.user, vigente=True)
        .exclude(fecha_estado__isnull=True)
        .values_list("fecha_estado", flat=True)[:1]
    )
    movimiento = producto.movimientos_banca.order_by("-fecha").values_list("fecha", flat=True).first()
    return max([f for f in [*fechas, movimiento] if f], default=None)


@transaction.atomic
def asegurar_reglas_automaticas(user) -> int:
    """Creates only source-keyed rules; repeated calls never duplicate them."""
    created = 0
    for producto in Producto.objects.filter(user=user, activo=True, tipo__in=("TDC", "CUENTA")):
        key = f"estado-cuenta:{producto.pk}"
        evidencia = _evidencia_producto(producto)
        regla, was_created = ReglaPendiente.objects.get_or_create(
            user=user,
            clave_fuente=key,
            defaults={
                "nombre": f"Actualizar {producto.nombre}",
                "tipo": "DOCUMENTO",
                "producto": producto,
                "periodicidad": "DESDE_ULTIMO_REGISTRO",
                "fecha_inicio": (evidencia + timedelta(days=30)) if evidencia else date.today(),
                "umbral_dias_inactividad": 30,
                "recordar_dias": 3,
                "contabiliza_gasto": False,
                "automatica": True,
                "url_pago": "/banca/importar/",
            },
        )
        if not was_created and regla.url_pago != "/banca/importar/":
            regla.url_pago = "/banca/importar/"
            regla.save(update_fields=["url_pago", "actualizada_at"])
        if was_created:
            created += 1
        materializar_ocurrencias(regla)

    for credencial in CredencialBanco.objects.filter(user=user, activo=True, conector__iexact="binance"):
        key = f"binance:{credencial.pk}"
        ultima = credencial.ultima_sincronizacion.date() if credencial.ultima_sincronizacion else date.today()
        regla, was_created = ReglaPendiente.objects.get_or_create(
            user=user,
            clave_fuente=key,
            defaults={
                "nombre": "Sincronizar Binance",
                "tipo": "SINCRONIZACION",
                "periodicidad": "DESDE_ULTIMO_REGISTRO",
                "fecha_inicio": ultima + timedelta(days=30),
                "producto": None,
                "umbral_dias_inactividad": 30,
                "contabiliza_gasto": False,
                "automatica": True,
                "url_pago": reverse("banca_sync_fuente_externa", args=[credencial.pk]),
            },
        )
        accion_binance = reverse("banca_sync_fuente_externa", args=[credencial.pk])
        if not was_created and regla.url_pago != accion_binance:
            regla.url_pago = accion_binance
            regla.save(update_fields=["url_pago", "actualizada_at"])
        if was_created:
            created += 1
        materializar_ocurrencias(regla)

    # Market and metadata are global operations and are exposed only to staff,
    # matching the existing altcoins permissions.
    if user.is_staff:
        try:
            from altcoins.models import AltcoinMarketSnapshot, AltcoinProject

            proyectos = AltcoinProject.objects.filter(active=True)
            if proyectos.exists():
                latest_market = AltcoinMarketSnapshot.objects.order_by("-observed_at").values_list("observed_at", flat=True).first()
                latest_metadata = proyectos.exclude(metadata_updated_at__isnull=True).order_by("-metadata_updated_at").values_list("metadata_updated_at", flat=True).first()
                for key, name, url, latest in (
                    ("coingecko:market", "Actualizar mercado (CoinGecko)", reverse("altcoins:run_operation", args=["market"]), latest_market.date() if latest_market else None),
                    ("coinmarketcap:metadata", "Validar metadata (CoinMarketCap)", reverse("altcoins:run_operation", args=["metadata"]), latest_metadata.date() if latest_metadata else None),
                ):
                    regla, was_created = ReglaPendiente.objects.get_or_create(
                        user=user,
                        clave_fuente=key,
                        defaults={
                            "nombre": name,
                            "tipo": "SINCRONIZACION",
                            "periodicidad": "DESDE_ULTIMO_REGISTRO",
                            "fecha_inicio": (latest + timedelta(days=30)) if latest else date.today(),
                            "umbral_dias_inactividad": 30,
                            "contabiliza_gasto": False,
                            "automatica": True,
                            "url_pago": url,
                        },
                    )
                    created += int(was_created)
                    materializar_ocurrencias(regla)
        except ImportError:
            pass
    return created
