"""Documentos y patrones que respaldan una revisión, sin modificar la deuda."""

from datetime import date
from decimal import Decimal

from django.db.models import Q

from banca.models import (
    ConciliacionEventoExterno, EventoFinancieroExterno, ImportacionEstadoCuenta,
    MovimientoBancario,
)
from configuracion.models import Producto
from patrimonio.models import DetalleCuentaPorCobrar
from patrimonio.services.cuentas_por_cobrar import (
    fecha_evento_neat, normalizar_referencia, primer_dia_mes,
    proponer_renovacion, ultimo_dia_mes, ventana_fuentes_cobro,
)


def transferencias_coincidentes(cuenta, abono):
    """Una coincidencia es una sugerencia; nunca una conciliación aprobada."""
    campo = "monto_clp" if cuenta.moneda == "CLP" else "monto"
    return list(MovimientoBancario.objects.filter(
        user_id=cuenta.user_id, producto__user_id=cuenta.user_id,
        fecha=abono.fecha, moneda=cuenta.moneda, monto__lt=0,
        **{campo: -abono.monto},
    ).exclude(estado="DESCARTADO_CIERRE").exclude(
        abonos_cuentas_por_cobrar__vigente=True,
        abonos_cuentas_por_cobrar__cuenta__user_id=cuenta.user_id,
    ).select_related("producto").order_by("pk")) + list(
        MovimientoBancario.objects.filter(
            user_id=cuenta.user_id, producto__user_id=cuenta.user_id,
            fecha=abono.fecha, moneda=cuenta.moneda, monto__lt=0,
            abonos_cuentas_por_cobrar__cuenta=cuenta,
            abonos_cuentas_por_cobrar__vigente=True,
            **{campo: -abono.monto},
        ).exclude(estado="DESCARTADO_CIERRE").select_related("producto").distinct().order_by("pk")
    )


def respaldos_cargo(cuenta, fuente):
    """Líneas que representan la misma fuente bancaria y su desglose Neat."""
    relaciones = ConciliacionEventoExterno.objects.filter(
        rol="CARGO", evento__user_id=cuenta.user_id,
        movimiento__user_id=cuenta.user_id, evento__moneda=cuenta.moneda,
        movimiento__moneda=cuenta.moneda,
    )
    detalles = cuenta.detalles.filter(vigente=True)
    if isinstance(fuente, MovimientoBancario):
        return list(detalles.filter(evento_externo_id__in=
            relaciones.filter(movimiento=fuente).values("evento_id")))
    return list(detalles.filter(movimiento_id__in=
        relaciones.filter(evento=fuente).values("movimiento_id")))


def coincidencias_neat_del_mes(cuenta, detalle):
    """Busca el pago del período aplicado cuando la fuente vinculada es antigua."""
    evento = detalle.evento_externo
    pago = getattr(evento, "pago_neat", None)
    if not pago:
        return []
    inicio, fin = ventana_fuentes_cobro(detalle.periodo)
    fecha = fecha_evento_neat(evento)
    if fecha and inicio <= fecha <= fin:
        return []
    cuenta_neat = normalizar_referencia(pago.cuenta)
    alias = normalizar_referencia((evento.metadata or {}).get("alias", ""))
    usados = DetalleCuentaPorCobrar.objects.filter(cuenta__user_id=cuenta.user_id, vigente=True).exclude(pk=detalle.pk)
    candidatos = []
    for candidato in (EventoFinancieroExterno.objects.filter(
        user_id=cuenta.user_id, moneda=cuenta.moneda, fuente="neat", monto_neto__gt=0,
    ).exclude(pk=evento.pk).exclude(estado__in=["FALLIDO", "REVERSADO"])
            .exclude(pk__in=usados.filter(evento_externo__isnull=False).values("evento_externo_id"))
            .select_related("pago_neat")):
        fecha_actual = fecha_evento_neat(candidato)
        pago_actual = getattr(candidato, "pago_neat", None)
        if not pago_actual or not fecha_actual or not inicio <= fecha_actual <= fin:
            continue
        if not cuenta_neat or normalizar_referencia(pago_actual.cuenta) != cuenta_neat:
            continue
        alias_actual = normalizar_referencia((candidato.metadata or {}).get("alias", ""))
        if alias and alias_actual and alias != alias_actual:
            continue
        if pago.tarjeta_ultimos4 and pago_actual.tarjeta_ultimos4 and pago.tarjeta_ultimos4 != pago_actual.tarjeta_ultimos4:
            continue
        candidatos.append(candidato)
    return candidatos


def correcciones_para_revision(cuenta, anterior):
    """Sólo sugiere una fuente distinta cuando hay una coincidencia única del mes."""
    if anterior is None:
        return []
    claves = anterior.desglose.get("contabilizados", {})
    correcciones = []
    for detalle in cuenta.detalles.filter(vigente=True, evento_externo__isnull=False).select_related("evento_externo__pago_neat"):
        if f"detalle:{detalle.pk}" not in claves:
            continue
        candidatos = coincidencias_neat_del_mes(cuenta, detalle)
        if len(candidatos) == 1:
            correcciones.append((detalle, candidatos[0]))
    return correcciones


def evidencia_calculo(cuenta, calculo):
    """Lee los importes de la foto histórica y añade evidencia consultable actual."""
    if calculo is None:
        return []
    detalles = {f"detalle:{d.pk}": d for d in cuenta.detalles.select_related("evento_externo", "movimiento")}
    abonos = {f"abono:{a.pk}": a for a in cuenta.abonos.select_related("movimiento")}
    filas = []
    for entrada in calculo.desglose.get("entradas", []):
        registro = detalles.get(entrada["clave"]) or abonos.get(entrada["clave"])
        registro = registro or detalles.get(f"detalle:{entrada.get('detalle_id')}") or abonos.get(f"abono:{entrada.get('abono_id')}")
        fila = dict(entrada)
        fila.update(fecha=date.fromisoformat(entrada["fecha"]),
                    monto=Decimal(entrada["monto"]), movimientos=[], coincidencias=[],
                    origen="Manual", estado="Revisar respaldo manual", anticipado=False)
        if registro:
            fila["periodo"] = date.fromisoformat(entrada["periodo"]) if entrada.get("periodo") else getattr(registro, "periodo", None) or calculo.periodo
            fila["anticipado"] = fila["fecha"] < fila["periodo"] and entrada["tipo"] == "CARGO"
            if registro.movimiento_id:
                fila["movimientos"] = [registro.movimiento]
                fila["origen"], fila["estado"] = "Banco", "Movimiento bancario vinculado"
            elif entrada["tipo"] == "ABONO":
                fila["coincidencias"] = transferencias_coincidentes(cuenta, registro)
                if fila["coincidencias"]:
                    fila["estado"] = "Transferencia coincidente · pendiente de conciliar"
            elif registro.evento_externo_id:
                evento = registro.evento_externo
                fila["origen"] = "NeatPagos"
                fila["fecha_neat"] = fecha_evento_neat(evento)
                inicio, fin = ventana_fuentes_cobro(fila["periodo"])
                fila["fuente_fuera_periodo"] = bool(fila["fecha_neat"] and not inicio <= fila["fecha_neat"] <= fin)
                fila["coincidencias_neat"] = coincidencias_neat_del_mes(cuenta, registro)
                fila["movimientos"] = list(MovimientoBancario.objects.filter(
                    user_id=cuenta.user_id, moneda=cuenta.moneda,
                    conciliaciones_externas__evento=evento,
                    conciliaciones_externas__rol="CARGO",
                ).exclude(estado="DESCARTADO_CIERRE").distinct())
                fila["estado"] = "Neat y banco relacionados" if fila["movimientos"] else "Neat disponible · falta respaldo bancario"
                if fila["fuente_fuera_periodo"]:
                    fila["estado"] = "Fuente Neat de otro período · revisar vinculación"
        for movimiento in fila["movimientos"] + fila["coincidencias"]:
            movimiento.estados_cobro = list(movimiento.importaciones.filter(
                user_id=cuenta.user_id, moneda=cuenta.moneda, estado="PROCESADO", vigente=True,
            ))
        filas.append(fila)
    return filas


def fuentes_para_revision(cuenta, periodo, anterior):
    """Propone respaldos del cálculo previo y transferencias de contrapartes conocidas."""
    candidatos = {}
    for fila in evidencia_calculo(cuenta, anterior):
        if fila["tipo"] == "ABONO" and len(fila["coincidencias"]) == 1:
            movimiento = fila["coincidencias"][0]
            candidatos[movimiento.pk] = ("ABONO", movimiento)
        elif fila["origen"] == "NeatPagos":
            for movimiento in fila["movimientos"]:
                candidatos[movimiento.pk] = ("CARGO", movimiento)
    nombres = {normalizar_referencia(a.movimiento.descripcion) for a in
               cuenta.abonos.filter(vigente=True, movimiento__isnull=False).select_related("movimiento")}
    nombres -= {"", "transferencia", "transferencia recibida", "abono"}
    for movimiento in MovimientoBancario.objects.filter(
        user_id=cuenta.user_id, producto__user_id=cuenta.user_id, moneda=cuenta.moneda,
        monto__lt=0, fecha__range=(periodo, ultimo_dia_mes(periodo)),
    ).exclude(estado="DESCARTADO_CIERRE").select_related("producto"):
        if normalizar_referencia(movimiento.descripcion) in nombres:
            candidatos[movimiento.pk] = ("ABONO", movimiento)
    return list(candidatos.values())


def asignaciones_para_revision(cuenta, periodo, anterior=None):
    """Reúne las asociaciones de esta cuenta, incluso antes de confirmar.

    La categoría describe el gasto; la asociación explícita identifica a quién
    se le cobra. Los registros incluidos o ignorados se muestran por separado.
    """
    inicio, fin = ventana_fuentes_cobro(periodo)
    incluidos = (anterior.desglose or {}).get("contabilizados", {}) if anterior else {}
    pendientes, contabilizados = [], []
    vistos = set()

    def agregar(registro, tipo, clave, movimiento, evento, partida=None):
        fuente = movimiento or evento
        if fuente and (fuente.user_id != cuenta.user_id or fuente.moneda != cuenta.moneda):
            return
        if movimiento and movimiento.producto.user_id != cuenta.user_id:
            return
        identidad = (tipo, "movimiento", movimiento.pk) if movimiento else (tipo, "neat", evento.pk) if evento else (tipo, clave)
        if identidad in vistos:
            return
        vistos.add(identidad)
        claves = {clave}
        if movimiento:
            claves.add(f"movimiento:{movimiento.pk}")
        if evento:
            claves.add(f"neat:{evento.pk}")
        incluido = tipo in {"CARGO", "ABONO"} and bool(claves & incluidos.keys())
        mes = partida.periodo if partida else getattr(registro, "periodo", None) or primer_dia_mes(registro.fecha)
        fila = dict(descripcion=registro.descripcion, tipo=tipo, monto=registro.monto,
                    fecha=registro.fecha, periodo=mes, partida_id=partida.pk if partida else None,
                    movimiento=movimiento, evento=evento, incluido=incluido,
                    anterior=registro.fecha < (periodo if tipo == "ABONO" else inicio),
                    otro_periodo=mes > periodo, trasladable=bool(partida and mes > periodo and registro.fecha <= fin),
                    origen="Banco" if movimiento else "NeatPagos" if evento else "Manual")
        (contabilizados if incluido else pendientes).append(fila)

    partidas = list(cuenta.partidas_revision.filter(resuelta_en__isnull=True)
                    .select_related("movimiento__producto", "evento_externo__pago_neat", "detalle", "abono"))
    for partida in partidas:
        clave = f"detalle:{partida.detalle_id}" if partida.detalle_id and partida.tipo != "ABONO" else f"abono:{partida.abono_id}" if partida.abono_id else partida.clave
        agregar(partida, partida.tipo, clave, partida.movimiento, partida.evento_externo, partida)
    for detalle in cuenta.detalles.filter(vigente=True, periodo__lte=periodo).select_related("movimiento__producto", "evento_externo__pago_neat"):
        agregar(detalle, "CARGO", f"detalle:{detalle.pk}", detalle.movimiento, detalle.evento_externo)
    for abono in cuenta.abonos.filter(vigente=True, fecha__lte=fin).select_related("movimiento__producto"):
        agregar(abono, "ABONO", f"abono:{abono.pk}", abono.movimiento, None)
    excluidos = list(cuenta.partidas_revision.filter(
        resuelta_en__isnull=False, decision="EXCLUIR", periodo__lte=periodo,
    ))
    return dict(asignaciones=pendientes, contabilizados=contabilizados, excluidos=excluidos)


def resumen_preparacion(cuenta, periodo, anterior=None):
    """Prioriza las fuentes asignadas; los documentos complementan la revisión."""
    periodo = primer_dia_mes(periodo)
    inicio, fin = ventana_fuentes_cobro(periodo)
    asignaciones = asignaciones_para_revision(cuenta, periodo, anterior)
    detalles = list(cuenta.detalles.filter(vigente=True, periodo__lte=periodo)
                    .select_related("movimiento", "evento_externo__pago_neat")
                    .order_by("-periodo", "-id"))
    abonos = list(cuenta.abonos.filter(vigente=True).select_related("movimiento"))
    # Un producto usado hace meses no exige su cartola para todos los meses
    # siguientes. Sólo las fuentes relevantes para este período orientan la carga.
    actuales = [d for d in detalles if d.periodo == periodo]
    filas_actuales = [f for f in asignaciones["asignaciones"] + asignaciones["contabilizados"]
                      if (f["partida_id"] or f["periodo"] == periodo)
                      and (periodo if f["tipo"] == "ABONO" else inicio) <= f["fecha"] <= fin]
    movimientos = [d.movimiento for d in actuales if d.movimiento_id]
    movimientos += [a.movimiento for a in abonos if a.movimiento_id and periodo <= a.fecha <= fin]
    movimientos += [f["movimiento"] for f in filas_actuales if f["movimiento"]]
    eventos_actuales = {d.evento_externo_id: d.evento_externo for d in actuales if d.evento_externo_id}
    eventos_actuales.update({f["evento"].pk: f["evento"] for f in filas_actuales if f["evento"]})
    productos = {m.producto_id for m in movimientos}
    productos.update(ConciliacionEventoExterno.objects.filter(
        evento_id__in=eventos_actuales,
        evento__user_id=cuenta.user_id, movimiento__user_id=cuenta.user_id, rol="CARGO",
        evento__moneda=cuenta.moneda, movimiento__moneda=cuenta.moneda,
    ).values_list("movimiento__producto_id", flat=True))
    tarjetas = {e.pago_neat.tarjeta_ultimos4 for e in eventos_actuales.values()
                if hasattr(e, "pago_neat") and e.pago_neat.tarjeta_ultimos4}
    productos.update(Producto.objects.filter(user_id=cuenta.user_id, tipo="TDC", ultimos4__in=tarjetas)
                     .values_list("pk", flat=True))
    estados = ImportacionEstadoCuenta.objects.filter(
        user_id=cuenta.user_id, moneda=cuenta.moneda, estado="PROCESADO", vigente=True,
        tipo_documento="ESTADO_CUENTA", producto__user_id=cuenta.user_id,
    ).select_related("producto").order_by("-fecha_estado", "-pk")
    del_mes = estados.filter(Q(fecha_estado__range=(periodo, fin)) |
                            Q(fecha_estado__isnull=True, periodo_fin__range=(periodo, fin)))
    documentos = []
    for tipo, titulo in [("TDC", "Estado de cuenta de tarjeta"), ("CUENTA", "Cartola de transferencias")]:
        esperados = set(Producto.objects.filter(user_id=cuenta.user_id, pk__in=productos, tipo=tipo)
                        .values_list("pk", flat=True))
        disponibles = del_mes.filter(tipo_producto=tipo)
        anteriores = estados.filter(tipo_producto=tipo)
        if esperados:
            disponibles = disponibles.filter(producto_id__in=esperados)
            anteriores = anteriores.filter(producto_id__in=esperados)
        disponibles = list(disponibles)
        cubiertos = {d.producto_id for d in disponibles}
        necesario = bool(esperados)
        faltan_productos = list(Producto.objects.filter(pk__in=esperados - cubiertos, user_id=cuenta.user_id))
        documentos.append(dict(tipo=tipo, titulo=titulo, disponibles=disponibles,
                               ultimo=anteriores.first(), necesario=necesario,
                               faltan_productos=faltan_productos,
                               completo=bool(disponibles) and (not esperados or esperados <= cubiertos)))
    eventos = list(EventoFinancieroExterno.objects.filter(
        user_id=cuenta.user_id, fuente="neat", moneda=cuenta.moneda,
    ).exclude(estado__in=["FALLIDO", "REVERSADO"]).select_related("pago_neat"))
    mes_patron = max((d.periodo for d in detalles), default=None)
    claves_historicas = {d.clave_similitud for d in actuales if d.evento_externo_id and d.clave_similitud}
    from patrimonio.services.cuentas_por_cobrar import clave_fuente
    claves_historicas.update(clave_fuente(e) for e in eventos_actuales.values())
    neat = [e for e in eventos if (fecha_evento_neat(e) and inicio <= fecha_evento_neat(e) <= fin)
            and (not claves_historicas or clave_fuente(e) in claves_historicas)]
    documentos.append(dict(tipo="NEAT", titulo="Reporte o comprobantes NeatPagos", disponibles=neat,
                           ultimo=None, necesario=bool(eventos_actuales),
                           completo=bool(neat) and (not claves_historicas or claves_historicas <= {clave_fuente(e) for e in neat})))
    renovacion = proponer_renovacion(cuenta, periodo)
    propuestas = {p["detalle_id"]: p for p in renovacion["propuestas"]}
    for detalle in detalles:
        candidatas = [f for f in filas_actuales if f["tipo"] == "CARGO" and not f["otro_periodo"]
                      and (f["movimiento"] or f["evento"])
                      and clave_fuente(f["movimiento"] or f["evento"]) == detalle.clave_similitud]
        if len(candidatas) == 1:
            propuestas.setdefault(detalle.pk, dict(monto=candidatas[0]["monto"], fecha=candidatas[0]["fecha"]))
    patrones = []
    vistos = set()
    for detalle in detalles:
        if detalle.periodo != mes_patron:
            continue
        clave = detalle.clave_similitud or normalizar_referencia(detalle.descripcion)
        if not detalle.movimiento_id and not detalle.evento_externo_id and (
            clave.startswith("saldo") or clave.startswith("monto pasado")
        ):
            continue
        if clave in vistos:
            continue
        vistos.add(clave)
        propuesta = propuestas.get(detalle.pk)
        actual = detalle.periodo == periodo
        advertencia = ""
        if actual and detalle.evento_externo_id:
            fecha_neat = fecha_evento_neat(detalle.evento_externo)
            if not fecha_neat or not inicio <= fecha_neat <= fin:
                actual = False
                candidatos = coincidencias_neat_del_mes(cuenta, detalle)
                propuesta = (dict(monto=candidatos[0].monto_neto, fecha=fecha_evento_neat(candidatos[0]))
                             if len(candidatos) == 1 else None)
                advertencia = "Posible corrección de fuente · pendiente de revisar" if propuesta else "Fuente fuera del mes · revisar vinculación"
        patrones.append(dict(descripcion=detalle.descripcion, periodo=detalle.periodo,
                             ultimo_monto=detalle.monto, nuevo_monto=detalle.monto if actual else propuesta["monto"] if propuesta else None,
                             fecha=detalle.fecha if actual else propuesta["fecha"] if propuesta else None,
                             origen=detalle.get_origen_display(), respaldado=actual or bool(propuesta), advertencia=advertencia))
    faltantes = [d["titulo"] for d in documentos if d["necesario"] and not d["completo"]]
    return dict(**asignaciones, documentos=documentos, patrones=patrones, faltantes=faltantes,
                inicio=inicio, fin=fin, evidencia=evidencia_calculo(cuenta, anterior))


def captura_preparacion(resumen):
    """Identidades y períodos de la evidencia, conservados con cada confirmación."""
    return dict(faltantes=resumen["faltantes"], asignaciones=[
        {campo: str(f[campo]) if campo in {"fecha", "periodo", "monto"} else f[campo]
         for campo in ["descripcion", "tipo", "monto", "fecha", "periodo", "partida_id", "incluido", "origen"]}
        for f in resumen["asignaciones"] + resumen["contabilizados"]], patrones=[
        {**p, "periodo": str(p["periodo"]), "fecha": str(p["fecha"]) if p["fecha"] else None,
         "ultimo_monto": str(p["ultimo_monto"]), "nuevo_monto": str(p["nuevo_monto"]) if p["nuevo_monto"] is not None else None}
        for p in resumen["patrones"]], documentos=[
            dict(tipo=d["tipo"], completo=d["completo"], ids=[r.pk for r in d["disponibles"]])
            for d in resumen["documentos"]])
