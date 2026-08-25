import logging
from datetime import date
from decimal import Decimal

import requests

from .models import TipoCambio

logger = logging.getLogger("configuracion.indicadores")


def fetch_mindicador_rates():
    """
    Descarga UF y USD del día desde mindicador.cl y hace upsert en TipoCambio.

    Prefiere verificar SSL; solo si el entorno tiene un problema de certificados (proxy
    corporativo, etc.) reintenta sin verificar, dejando aviso en el log.
    """
    try:
        data = _get_mindicador(verify=True)
    except requests.exceptions.SSLError as e:
        logger.warning(
            "SSL falló contra mindicador.cl, reintentando sin verificar: %s", e
        )
        try:
            data = _get_mindicador(verify=False)
        except Exception as e2:
            logger.error("Error obteniendo indicadores: %s", e2)
            return None, False
    except Exception as e:
        logger.error("Error obteniendo indicadores: %s", e)
        return None, False

    try:
        uf_val = Decimal(str(data["uf"]["valor"]))
        dolar_val = Decimal(str(data["dolar"]["valor"]))
    except (KeyError, TypeError, ValueError) as e:
        logger.error("Respuesta de mindicador.cl inesperada: %s", e)
        return None, False

    tc, _ = TipoCambio.objects.update_or_create(
        fecha=date.today(),
        fuente="mindicador.cl",
        defaults={"uf": uf_val, "dolar": dolar_val},
    )
    return tc, True


def _get_mindicador(*, verify: bool) -> dict:
    resp = requests.get("https://mindicador.cl/api", timeout=10, verify=verify)
    resp.raise_for_status()
    return resp.json()
