"""
Reconciliación: asigna la `naturaleza` (OPERACIONAL vs TRANSFERENCIA) y vincula la
bicicleta financiera a `prestamos.Prestamo`, para que esos flujos NO ensucien ingresos
ni patrimonio (ver §17).

Reglas:
  1. Pago de TDC (abono "PAGO" en una tarjeta) -> TRANSFERENCIA (saldar tarjeta).
  2. Contraparte conocida (alias/RUT en la glosa) -> TRANSFERENCIA + vínculo a su préstamo.
  3. Cargo NeatPagos cuyo monto ≈ (principal + comisión) de un préstamo activo
     -> TRANSFERENCIA + vínculo al préstamo (tolerancia configurable).
  4. Abono no reconocido -> `por_revisar=True` (NUNCA se cuenta como ingreso por defecto).
"""

from __future__ import annotations

import re
from decimal import Decimal

from django.db.models import Sum

from banca.models import ContraparteConocida, MovimientoBancario

_RE_PAGO = re.compile(r"\bPAGO\b", re.I)
_RE_NEAT = re.compile(r"NEAT", re.I)
_RE_TRANSFER = re.compile(r"TRANSFER|TEF|ABONO|DEP[OÓ]SITO", re.I)


def _suma_cargos_vinculados(prestamo, *, exclude_id=None) -> Decimal:
    """Suma de cargos (montos positivos) ya vinculados a un préstamo."""
    qs = MovimientoBancario.objects.filter(prestamo=prestamo, monto_clp__gt=0)
    if exclude_id is not None:
        qs = qs.exclude(id=exclude_id)
    return qs.aggregate(s=Sum("monto_clp"))["s"] or Decimal("0")


def _prestamos_que_caben(mov, prestamos, tol: Decimal) -> list:
    """
    Préstamos activos a los que este cargo NEAT podría pertenecer: el préstamo aún no está
    lleno y el cargo cabe dentro de su capacidad (monto_total + tolerancia de comisión).
    Si el préstamo fija una tarjeta, debe coincidir con el producto del movimiento.
    """
    candidatos = []
    for p in prestamos:
        total = Decimal(p.monto_total or 0)
        if total <= 0:
            continue
        if getattr(p, "tarjeta_asociada_id", None) and p.tarjeta_asociada_id != mov.producto_id:
            continue
        ya = _suma_cargos_vinculados(p, exclude_id=mov.id)
        capacidad = total * (Decimal("1") + tol)
        if ya < total and (ya + mov.monto_clp) <= capacidad:
            candidatos.append(p)
    return candidatos


def _tolerancia(user) -> Decimal:
    from banca.models import ConfiguracionBanca

    cfg = ConfiguracionBanca.objects.filter(user=user).first()
    return cfg.tolerancia_comision_bicicleta if cfg else Decimal("0.03")


def _prestamos_activos(user):
    try:
        from prestamos.models import Prestamo

        return list(Prestamo.objects.filter(user=user, activo=True))
    except Exception:
        return []


def reconciliar(user, ids: list[int] | None = None) -> dict:
    """Aplica las reglas de naturaleza/bicicleta. Devuelve conteos."""
    qs = MovimientoBancario.objects.filter(user=user)
    if ids is not None:
        qs = qs.filter(id__in=ids)
    # no tocar la naturaleza/préstamo que el usuario fijó a mano
    qs = qs.filter(naturaleza_es_manual=False)

    contrapartes = list(ContraparteConocida.objects.filter(user=user, activo=True))
    prestamos = _prestamos_activos(user)
    tol = _tolerancia(user)

    transferencias, por_revisar = 0, 0
    for mov in qs:
        desc = mov.descripcion or ""
        nat, prestamo, revisar = "OPERACIONAL", None, False

        # 1. Pago de TDC
        if _RE_PAGO.search(desc) and not mov.es_cargo:
            nat = "TRANSFERENCIA"

        # 2. Contraparte conocida
        if nat == "OPERACIONAL":
            for cp in contrapartes:
                if (cp.alias and cp.alias.lower() in desc.lower()) or (cp.rut and cp.rut in desc):
                    nat, prestamo = "TRANSFERENCIA", cp.prestamo
                    break

        # 3. Cargo NeatPagos hacia un préstamo (bicicleta). Como NEAT limita a ~3MM, un
        #    préstamo grande se parte en varios cargos: ACUMULAMOS hacia el monto total.
        #    - exactamente 1 préstamo con capacidad -> auto-vincula (TRANSFERENCIA)
        #    - varios préstamos posibles -> 'por revisar' (asignación manual)
        #    - ninguno con capacidad -> queda OPERACIONAL (posible pago real vía Neat)
        if nat == "OPERACIONAL" and mov.es_cargo and _RE_NEAT.search(desc):
            candidatos = _prestamos_que_caben(mov, prestamos, tol)
            if len(candidatos) == 1:
                nat, prestamo = "TRANSFERENCIA", candidatos[0]
            elif len(candidatos) > 1:
                revisar = True

        # 4. Abono ambiguo -> por revisar (jamás ingreso silencioso)
        if nat == "OPERACIONAL" and not mov.es_cargo and _RE_TRANSFER.search(desc):
            revisar = True

        cambios = []
        if mov.naturaleza != nat:
            mov.naturaleza = nat
            cambios.append("naturaleza")
        if prestamo and mov.prestamo_id != prestamo.id:
            mov.prestamo = prestamo
            cambios.append("prestamo")
        if mov.por_revisar != revisar:
            mov.por_revisar = revisar
            cambios.append("por_revisar")
        if cambios:
            mov.save(update_fields=cambios + ["updated_at"])
            if nat == "TRANSFERENCIA":
                transferencias += 1
            if revisar:
                por_revisar += 1

    return {"transferencias": transferencias, "por_revisar": por_revisar}
