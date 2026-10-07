"""
Reconciliación: asigna la `naturaleza` (OPERACIONAL vs TRANSFERENCIA) y vincula la
bicicleta financiera a `prestamos.Prestamo`, para que esos flujos NO ensucien ingresos
ni patrimonio (ver §17).

Reglas:
  1. Pago de TDC (abono "PAGO" en una tarjeta) -> TRANSFERENCIA (saldar tarjeta).
  2. Contraparte conocida (alias/RUT en la glosa) -> TRANSFERENCIA + vínculo a su préstamo.
  3. Cargo NeatPagos cuyo monto ≈ (principal + comisión) de un préstamo activo
     -> TRANSFERENCIA + vínculo al préstamo (tolerancia configurable).
  4. Abono desconocido o traspaso saliente de cuenta corriente sin titular reconocido
     -> `por_revisar=True` hasta que el usuario defina ingreso, gasto o transferencia.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from itertools import combinations

from django.db.models import Sum

from banca.models import (
    ConciliacionEventoExterno,
    ContraparteConocida,
    EventoFinancieroExterno,
    MapeoComercio,
    MovimientoBancario,
)

_RE_PAGO = re.compile(r"\bPAGO\b|\bMONTO\s+CANCELADO\b", re.I)
_RE_NEAT = re.compile(r"NEAT", re.I)
_RE_TRANSFER = re.compile(r"TRANSF|TRASPASO|TEF|ABONO|DEP[OÓ]SITO", re.I)
_RE_PRODUCTO_PROPIO = re.compile(
    r"PAGO\s+(?:TARJETA|PRESTAMO|PR[ÉE]STAMO|CUOTA\s+CR[ÉE]DITO)"
    r"|TRANSF.*PAGO\s+TARJETA|TRASPASO.*(?:T\.\s*CR[ÉE]DITO|CTA\.?\s*CTE|L[IÍ]NEA\s+CR[ÉE]DITO)"
    r"|(?:ABONO\s+DESDE|CARGO\s+CTACTE\s+POR\s+TRASPASO).*L[IÍ]NEA\s+DE\s+CR[ÉE]DITO",
    re.I,
)


def _suma_cargos_vinculados(prestamo, *, exclude_id=None) -> Decimal:
    """Suma de cargos (montos positivos) ya vinculados a un préstamo."""
    qs = MovimientoBancario.objects.filter(prestamo=prestamo, monto_clp__gt=0).exclude(
        estado="DESCARTADO_CIERRE"
    )
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


_TOKENS_NOMBRE_IGNORADOS = {
    "de", "del", "la", "las", "los", "y", "a", "para", "transf", "transferencia",
}


def _tokens_persona(valor: str) -> set[str]:
    normalizado = unicodedata.normalize("NFKD", valor or "")
    normalizado = "".join(c for c in normalizado if not unicodedata.combining(c)).lower()
    return {
        token
        for token in re.findall(r"[a-z]{3,}", normalizado)
        if token not in _TOKENS_NOMBRE_IGNORADOS
    }


def _nombre_aparece(nombre: str, descripcion: str) -> bool:
    nombre_tokens = _tokens_persona(nombre)
    descripcion_tokens = _tokens_persona(descripcion)
    return len(nombre_tokens) >= 2 and nombre_tokens.issubset(descripcion_tokens)


def _nombre_bancario_compatible(nombre: str, descripcion: str) -> bool:
    """Tolera nombres bancarios truncados y nombres de una sola palabra."""
    nombre_tokens = _tokens_persona(nombre)
    descripcion_tokens = _tokens_persona(descripcion)
    if len(nombre_tokens & descripcion_tokens) >= 2:
        return True
    primer_nombre = next(iter(re.findall(r"[A-Za-zÁÉÍÓÚáéíóú]{3,}", nombre or "")), "")
    return bool(primer_nombre and normalizar_nombre(primer_nombre) in descripcion_tokens)


def normalizar_nombre(valor: str) -> str:
    normalizado = unicodedata.normalize("NFKD", valor or "")
    return "".join(c for c in normalizado if not unicodedata.combining(c)).lower()


def _firmas_cuentas_propias(user) -> list[set[str]]:
    """Infiere el titular usando traspasos espejados entre sus cuentas corrientes."""
    movimientos = list(
        MovimientoBancario.objects.filter(
            user=user,
            producto__tipo="CUENTA",
        )
        .exclude(estado="DESCARTADO_CIERRE")
        .only("id", "producto_id", "fecha", "monto_clp", "descripcion")
    )
    firmas = []
    for indice, primero in enumerate(movimientos):
        if not _RE_TRANSFER.search(primero.descripcion or ""):
            continue
        for segundo in movimientos[indice + 1 :]:
            if (
                primero.producto_id == segundo.producto_id
                or primero.fecha != segundo.fecha
                or primero.monto_clp != -segundo.monto_clp
                or not _RE_TRANSFER.search(segundo.descripcion or "")
            ):
                continue
            comunes = _tokens_persona(primero.descripcion) & _tokens_persona(segundo.descripcion)
            if len(comunes) >= 2 and comunes not in firmas:
                firmas.append(comunes)
    return firmas


def _es_movimiento_de_producto_propio(movimiento, firmas_propias) -> bool:
    if movimiento.producto.tipo != "CUENTA":
        return False
    descripcion = movimiento.descripcion or ""
    if _RE_PRODUCTO_PROPIO.search(descripcion):
        return True
    tokens = _tokens_persona(descripcion)
    if any(len(tokens & firma) >= 2 for firma in firmas_propias):
        return True
    titular = f"{movimiento.user.first_name} {movimiento.user.last_name}".strip()
    return bool(_RE_TRANSFER.search(descripcion) and _nombre_aparece(titular, descripcion))


def _contraparte_en_neat(movimiento, contrapartes):
    """Busca si el destinatario de un cargo Neat es una contraparte del usuario."""
    estados_validos = {"CONFIRMADO", "EN_PROCESO", "REQUIERE_REVISION"}
    for conciliacion in movimiento.conciliaciones_externas.all():
        evento = conciliacion.evento
        if (
            conciliacion.rol != "CARGO"
            or evento.user_id != movimiento.user_id
            or evento.fuente != "neat"
            or evento.estado not in estados_validos
        ):
            continue
        pago = getattr(evento, "pago_neat", None)
        destinatario = (pago.destinatario if pago else "").strip()
        if not destinatario:
            continue
        for contraparte in contrapartes:
            if (
                (contraparte.rut and contraparte.rut in destinatario)
                or (
                    contraparte.alias
                    and _nombre_bancario_compatible(contraparte.alias, destinatario)
                )
            ):
                return contraparte
    return None


def _monto_neat_transferido(evento) -> Decimal:
    """Principal pagado al destinatario, excluyendo la comisión cargada a la TDC."""
    pago = getattr(evento, "pago_neat", None)
    return Decimal(
        (pago.monto_pagado if pago else None)
        or evento.monto_bruto
        or evento.monto_neto
        or 0
    )


def _subset_neat_para_abono(user, movimiento, tolerancia):
    """Encuentra pagos Neat previos del mismo destinatario que explican un abono."""
    desde = movimiento.fecha
    dias = 0
    while dias < 5:
        desde = desde.fromordinal(desde.toordinal() - 1)
        if desde.weekday() < 5:
            dias += 1
    eventos = list(
        EventoFinancieroExterno.objects.filter(
            user=user,
            fuente="neat",
            fecha_evento__range=(desde, movimiento.fecha),
        )
        .exclude(estado__in=("FALLIDO", "REVERSADO"))
        .exclude(conciliaciones__rol="DEPOSITO")
        .select_related("pago_neat")
        .order_by("fecha_evento", "id")
    )
    por_destinatario = {}
    for evento in eventos:
        pago = getattr(evento, "pago_neat", None)
        destinatario = (pago.destinatario if pago else "").strip()
        if destinatario and _nombre_bancario_compatible(destinatario, movimiento.descripcion):
            por_destinatario.setdefault(destinatario.casefold(), []).append(evento)

    objetivo = abs(movimiento.monto_clp)
    mejor = None
    for grupo in por_destinatario.values():
        for cantidad in range(1, min(len(grupo), 6) + 1):
            for subconjunto in combinations(grupo, cantidad):
                total = sum(
                    (_monto_neat_transferido(evento) for evento in subconjunto),
                    Decimal("0"),
                )
                diferencia = abs(total - objetivo)
                if objetivo and diferencia <= objetivo * tolerancia:
                    clave = (diferencia, cantidad, tuple(evento.pk for evento in subconjunto))
                    if mejor is None or clave < mejor[0]:
                        mejor = (clave, subconjunto)
    return list(mejor[1]) if mejor else []


def _prestamo_por_destinatario(prestamos, eventos):
    candidatos = []
    for evento in eventos:
        tokens_destinatario = _tokens_persona(evento.pago_neat.destinatario)
        for prestamo in prestamos:
            tokens_prestamo = _tokens_persona(prestamo.nombre) - {"bicicleta", "prestamo"}
            if tokens_destinatario & tokens_prestamo and prestamo not in candidatos:
                candidatos.append(prestamo)
    return candidatos[0] if len(candidatos) == 1 else None


def reconciliar(user, ids: list[int] | None = None) -> dict:
    """Aplica las reglas de naturaleza/bicicleta. Devuelve conteos."""
    qs = MovimientoBancario.objects.filter(user=user).exclude(estado="DESCARTADO_CIERRE")
    qs = qs.select_related("user", "producto").prefetch_related(
        "conciliaciones_externas__evento__pago_neat"
    )
    if ids is not None:
        qs = qs.filter(id__in=ids)
    # no tocar la naturaleza/préstamo que el usuario fijó a mano
    qs = qs.filter(naturaleza_es_manual=False)

    contrapartes = list(ContraparteConocida.objects.filter(user=user, activo=True))
    mapeos_ingreso = list(
        MapeoComercio.objects.filter(
            user=user,
            categoria__aplica_a__in=("INGRESO", "AMBOS"),
            categoria__activa=True,
        ).select_related("categoria")
    )
    prestamos = _prestamos_activos(user)
    tol = _tolerancia(user)
    firmas_propias = _firmas_cuentas_propias(user)

    transferencias, por_revisar = 0, 0
    pagos_neat_devuelto = set()
    for mov in qs:
        desc = mov.descripcion or ""
        nat, prestamo, revisar = "OPERACIONAL", None, False
        desc_normalizada = normalizar_nombre(desc)
        ingreso_reconocido = not mov.es_cargo and any(
            mapeo.patron and mapeo.patron in desc_normalizada
            for mapeo in mapeos_ingreso
        )

        # 0. Un vínculo de préstamo ya resuelto no se deshace en ejecuciones posteriores.
        if mov.prestamo_id:
            nat, prestamo = "TRANSFERENCIA", mov.prestamo

        # Un abono ya explicado por Neat conserva la decisión y no consume otro
        # subconjunto de eventos al recalcular.
        elif mov.conciliaciones_externas.filter(
            evento__fuente="neat", rol="DEPOSITO"
        ).exists():
            nat = "TRANSFERENCIA"

        # El movimiento bancario sólo muestra "NEAT PAGOS"; el destinatario
        # conciliado permite reconocer una bicicleta configurada por el usuario.
        elif mov.es_cargo and (contraparte_neat := _contraparte_en_neat(mov, contrapartes)):
            nat, prestamo = "TRANSFERENCIA", contraparte_neat.prestamo

        # Los pagos de productos financieros y los traspasos cuyo titular fue
        # confirmado por un movimiento espejo no son consumo ni ingreso.
        elif _es_movimiento_de_producto_propio(mov, firmas_propias):
            nat = "TRANSFERENCIA"

        # 1. Pago de TDC
        elif _RE_PAGO.search(desc) and not mov.es_cargo:
            nat = "TRANSFERENCIA"

        # 2. Contraparte conocida
        if nat == "OPERACIONAL":
            for cp in contrapartes:
                if (
                    (cp.alias and _nombre_bancario_compatible(cp.alias, desc))
                    or (cp.rut and cp.rut in desc)
                ):
                    nat, prestamo = "TRANSFERENCIA", cp.prestamo
                    break

        # 2b. Abono que devuelve una bicicleta originada en NeatPagos. El nombre
        # puede venir truncado o con apellidos invertidos, por eso se comparan
        # tokens y además se exige que el monto calce con un subconjunto cercano.
        eventos_devolucion = []
        if nat == "OPERACIONAL" and not mov.es_cargo:
            eventos_devolucion = _subset_neat_para_abono(user, mov, tol)
            if eventos_devolucion:
                nat = "TRANSFERENCIA"
                prestamo = _prestamo_por_destinatario(prestamos, eventos_devolucion)

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

        # 4. Abono ambiguo o traspaso saliente de cuenta corriente -> revisión manual.
        if (
            nat == "OPERACIONAL"
            and _RE_TRANSFER.search(desc)
            and (
                (not mov.es_cargo and not ingreso_reconocido)
                or (mov.es_cargo and mov.producto.tipo == "CUENTA")
            )
        ):
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
        if nat == "TRANSFERENCIA" and not mov.categoria_es_manual and mov.categoria_id:
            mov.categoria = None
            mov.confianza_ia = None
            mov.clasificado_at = None
            mov.fuente_clasificacion = "PENDIENTE"
            cambios.extend(
                ["categoria", "confianza_ia", "clasificado_at", "fuente_clasificacion"]
            )
        if cambios:
            mov.save(update_fields=cambios + ["updated_at"])
            if nat == "TRANSFERENCIA":
                transferencias += 1
            if revisar:
                por_revisar += 1
        if eventos_devolucion:
            pagos_neat_devuelto.update(evento.pk for evento in eventos_devolucion)
            for evento in eventos_devolucion:
                ConciliacionEventoExterno.objects.get_or_create(
                    evento=evento,
                    movimiento=mov,
                    rol="DEPOSITO",
                    defaults={
                        "monto_asignado": _monto_neat_transferido(evento),
                        "confianza": Decimal("0.900"),
                        "metodo": "MONTO_FECHA",
                    },
                )

    # Si una persona devolvió el principal, el cargo Neat de origen tampoco fue
    # gasto operacional.
    if pagos_neat_devuelto:
        cargos_neat = (
            MovimientoBancario.objects.filter(
                user=user,
                naturaleza_es_manual=False,
                conciliaciones_externas__evento__user=user,
                conciliaciones_externas__evento_id__in=pagos_neat_devuelto,
                conciliaciones_externas__rol="CARGO",
            )
            .distinct()
        )
        for cargo in cargos_neat:
            cambios = []
            if cargo.naturaleza != "TRANSFERENCIA":
                cargo.naturaleza = "TRANSFERENCIA"
                cambios.append("naturaleza")
            if cargo.por_revisar:
                cargo.por_revisar = False
                cambios.append("por_revisar")
            if not cargo.categoria_es_manual and cargo.categoria_id:
                cargo.categoria = None
                cargo.confianza_ia = None
                cargo.clasificado_at = None
                cargo.fuente_clasificacion = "PENDIENTE"
                cambios.extend(
                    ["categoria", "confianza_ia", "clasificado_at", "fuente_clasificacion"]
                )
            if cambios:
                cargo.save(update_fields=cambios + ["updated_at"])
                transferencias += 1

    return {"transferencias": transferencias, "por_revisar": por_revisar}
