"""Vista mensual de los registros guardados, sin confirmar ni modificar saldos."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlencode

from django.urls import reverse

from patrimonio.services.cuentas_por_cobrar import formatear_monto, primer_dia_mes
from patrimonio.services.preparacion_cobros import respaldos_cargo

ZERO = Decimal("0")


def _clave(partida):
    if partida.tipo == "ABONO" or partida.tipo == "RETIRAR_ABONO":
        return f"abono:{partida.abono_id}" if partida.abono_id else partida.clave
    return f"detalle:{partida.detalle_id}" if partida.detalle_id else partida.clave


def _marcas(fila):
    return {f"{nombre}:{fila[campo]}" for nombre, campo in
            [("movimiento", "movimiento_id"), ("neat", "evento_externo_id")]
            if fila.get(campo)}


def _fila(tipo, clave, periodo, fecha, monto, descripcion, **datos):
    return dict(tipo=tipo, clave=clave, periodo=periodo, fecha=fecha,
                monto=abs(monto), efecto=-monto if tipo == "ABONO" else monto,
                descripcion=descripcion or "Abono", pendiente=False, **datos)


def _texto(cuenta, resumen):
    lineas = [f"Detalle pendiente: {cuenta.nombre}"]
    if cuenta.descripcion:
        lineas.append(cuenta.descripcion)
    if resumen.saldo_anterior:
        lineas.append(f"- Saldo pendiente anterior: {formatear_monto(resumen.saldo_anterior, cuenta.moneda)}")
    for fila in resumen.entradas:
        if fila["tipo"] == "ABONO" or not fila["efecto"]:
            continue
        importe = formatear_monto(fila["efecto"], cuenta.moneda)
        prefijo = "Ajuste · " if fila["tipo"] != "CARGO" else ""
        lineas.append(f"- {prefijo}{fila['descripcion']}: {importe}")
    if resumen.abonos:
        lineas.append(f"Abonos recibidos: -{formatear_monto(resumen.abonos, cuenta.moneda)}")
    lineas.append(f"Total pendiente: {formatear_monto(max(resumen.saldo_final, ZERO), cuenta.moneda)}")
    if resumen.saldo_final < 0:
        lineas.append(f"Abonos en exceso: {formatear_monto(-resumen.saldo_final, cuenta.moneda)}")
    return "\n".join(lineas)


def resumen_mensual(cuenta, periodo, *, incluir_pendientes=True, calculo_pendiente=None):
    """Arrastra sólo efectos hasta el mes consultado; nunca utiliza saldos futuros.

    La vista provisional reconstruye los registros de cada mes, sin arrastrar
    referencias históricas opcionales. La modalidad sin pendientes genera el
    texto oficial desde las versiones confirmadas. Consultar nunca escribe.
    """
    periodo = primer_dia_mes(periodo)
    calculos = list(cuenta.calculos.all())
    if calculo_pendiente is not None:
        calculos = [calculo_pendiente] + [c for c in calculos if c.pk != calculo_pendiente.pk]
    confirmado = (next((c for c in calculos if c.estado == "CONFIRMADO" and c.periodo <= periodo), None)
                  if not incluir_pendientes else None)
    exacto = confirmado is not None and confirmado.periodo == periodo
    incluidos = confirmado.desglose.get("contabilizados", {}) if confirmado else {}
    partidas = list(cuenta.partidas_revision.select_related(
        "detalle", "abono", "conciliar_con", "movimiento", "evento_externo", "resuelta_en__anterior",
    ).all())
    asientos = {e["id"]: e for c in calculos if c.estado == "CONFIRMADO"
                for e in c.desglose.get("entradas", []) if e.get("id")}
    claves_confirmadas = {clave for c in calculos if c.estado == "CONFIRMADO"
                          for clave in c.desglose.get("contabilizados", {})}
    solo_respaldo = set()
    retiros_diferidos = set()
    for partida in partidas:
        if not partida.resuelta_en_id:
            continue
        if partida.decision == "RESPALDO":
            base = partida.resuelta_en.anterior
            if _clave(partida) not in (base.desglose.get("contabilizados", {}) if base else {}):
                solo_respaldo.add(_clave(partida))
        registro = partida.detalle or partida.abono
        if (registro and partida.decision == "APLICAR" and partida.tipo.startswith("RETIRAR")
                and partida.periodo_mensual and partida.periodo_mensual !=
                (registro.periodo or primer_dia_mes(registro.fecha))):
            retiros_diferidos.add(_clave(partida))
    efectos = {}
    marcas = set(incluidos)
    for tipo, registros, prefijo in [("CARGO", cuenta.detalles.all(), "detalle"),
                                      ("ABONO", cuenta.abonos.all(), "abono")]:
        for registro in registros:
            if not incluir_pendientes:
                continue
            clave = f"{prefijo}:{registro.pk}"
            if clave in solo_respaldo or (not registro.vigente and clave not in retiros_diferidos):
                continue
            fecha = registro.fecha
            mes = registro.periodo or primer_dia_mes(fecha)
            if mes > periodo:
                continue
            fuente = dict(movimiento_id=registro.movimiento_id,
                          evento_externo_id=getattr(registro, "evento_externo_id", None))
            if _marcas(fuente) & marcas:
                continue
            fila = _fila(tipo, clave, mes, fecha, registro.monto, registro.descripcion,
                         detalle_id=registro.pk if tipo == "CARGO" else None,
                         abono_id=registro.pk if tipo == "ABONO" else None,
                         confirmada=clave in claves_confirmadas,
                         provisional=getattr(registro, "provisional", False), **fuente)
            efectos[clave] = fila
            marcas.update(_marcas(fila))

    avisos = []
    # Un ajuste no crea un cargo materializado. Se conserva una sola vez en
    # el mes elegido. Las ediciones deliberadamente diferidas mantienen su
    # diferencia en ese mes; una reconstrucción del mes original queda allí.
    for partida in partidas if incluir_pendientes else []:
        if not partida.resuelta_en_id or partida.decision != "APLICAR":
            continue
        importe = partida.monto
        if partida.tipo.startswith("RETIRAR") and _clave(partida) in retiros_diferidos:
            importe = partida.monto if partida.tipo == "RETIRAR_ABONO" else -partida.monto
        elif partida.tipo == "EDITAR" and partida.periodo_mensual and partida.detalle_id:
            if partida.periodo_mensual == partida.detalle.periodo:
                continue
            asiento = asientos.get(partida.pk, {})
            registro_original = asiento.get("registro_original", {})
            original = Decimal(registro_original.get("monto", str(partida.monto - Decimal(asiento.get("efecto", "0")))))
            importe = partida.monto - original
            actual = efectos.get(_clave(partida))
            if actual:
                actual["monto"] -= importe
                actual["efecto"] -= importe
                if periodo < partida.periodo_mensual and registro_original:
                    actual["descripcion"] = registro_original["descripcion"]
                    actual["fecha"] = date.fromisoformat(registro_original["fecha"])
        elif partida.tipo != "AJUSTE":
            continue
        if partida.periodo_vista <= periodo:
            efectos[f"partida:{partida.pk}"] = _fila(
                "AJUSTE", f"partida:{partida.pk}", partida.periodo_vista, partida.fecha,
                importe, partida.descripcion, confirmada=True,
            )
    for partida in partidas:
        if not incluir_pendientes:
            break
        mes_partida = partida.periodo_vista
        if partida.resuelta_en_id or mes_partida > periodo:
            continue
        clave = _clave(partida)
        if partida.decision == "EXCLUIR":
            if partida.tipo in {"CARGO", "ABONO"}:
                efectos.pop(clave, None)
            continue
        if partida.decision == "RESPALDO":
            continue
        if not partida.registrada_por_usuario and partida.decision not in {"APLICAR", "CONCILIAR"}:
            continue
        actual = efectos.get(clave)
        if partida.tipo == "ABONO" and partida.decision == "CONCILIAR" and partida.conciliar_con_id:
            manual = f"abono:{partida.conciliar_con_id}"
            banco = f"abono:{partida.abono_id}" if partida.abono_id else partida.clave
            efectos.pop(banco, None)
            descontado = sum((Decimal(incluidos.get(k, "0")) for k in {manual, banco}), ZERO)
            importe = -partida.monto - descontado if manual in incluidos else ZERO
            tipo = "AJUSTE"
        elif partida.tipo == "EDITAR":
            if actual and actual["periodo"] == mes_partida:
                actual.update(monto=partida.monto, efecto=partida.monto, descripcion=partida.descripcion,
                              fecha=partida.fecha, pendiente=True, partida_id=partida.pk,
                              provisional=partida.provisional,
                              monto_original=actual["monto"])
                continue
            valor = Decimal(incluidos.get(clave, "0")) if clave in incluidos else actual["efecto"] if actual else ZERO
            importe, tipo = partida.monto - valor, "AJUSTE"
        elif partida.tipo.startswith("RETIRAR"):
            valor = Decimal(incluidos.get(clave, "0")) if clave in incluidos else actual["efecto"] if actual else ZERO
            if actual and actual["periodo"] == mes_partida:
                efectos.pop(clave)
                continue
            importe, tipo = -valor, "AJUSTE"
        elif partida.tipo in {"CARGO", "ABONO"}:
            if actual or clave in incluidos:
                continue
            datos_fuente = dict(movimiento_id=partida.movimiento_id, evento_externo_id=partida.evento_externo_id)
            if _marcas(datos_fuente) & marcas:
                continue
            fuente = partida.movimiento or partida.evento_externo
            if partida.tipo == "CARGO" and fuente and any(
                f"detalle:{d.pk}" in incluidos or f"detalle:{d.pk}" in efectos
                for d in respaldos_cargo(cuenta, fuente)
            ):
                avisos.append(f"{partida.descripcion}: comparte respaldo con un cargo ya incluido; no se suma otra vez.")
                continue
            importe, tipo = (-partida.monto if partida.tipo == "ABONO" else partida.monto), partida.tipo
        else:
            importe, tipo = partida.monto, "AJUSTE"
        fila = _fila(tipo, f"partida:{partida.pk}", mes_partida, partida.fecha,
                     abs(importe) if tipo == "ABONO" else importe, partida.descripcion,
                     partida_id=partida.pk, detalle_id=partida.detalle_id, abono_id=partida.abono_id,
                     provisional=partida.provisional,
                     movimiento_id=partida.movimiento_id, evento_externo_id=partida.evento_externo_id)
        fila["pendiente"] = True
        if (partida.registrada_por_usuario and partida.tipo in {"CARGO", "ABONO"}
                and not partida.detalle_id and not partida.abono_id
                and not partida.movimiento_id and not partida.evento_externo_id):
            fila["editar_partida_id"] = partida.pk
        efectos[fila["clave"]] = fila
        marcas.update(_marcas(fila))

    versiones = sorted((c for c in calculos if c.estado == "CONFIRMADO" and c.periodo == periodo
                        and not incluir_pendientes), key=lambda c: c.revision)
    saldo_anterior = (versiones[0].saldo_anterior if exacto else confirmado.saldo_final) if confirmado else ZERO
    cargos = sum((c.cargos for c in versiones), ZERO)
    abonos = sum((c.abonos for c in versiones), ZERO)
    ajustes = sum((c.ajustes for c in versiones), ZERO)
    entradas = []
    referencia_desglosada = False
    if exacto:
        base = versiones[0].anterior
        if base and base.estado == "REFERENCIA" and not base.referencia_usuario and base.periodo == periodo:
            registros = {f"detalle:{d.pk}": d for d in cuenta.detalles.all()}
            registros.update({f"abono:{a.pk}": a for a in cuenta.abonos.all()})
            for e in base.desglose.get("entradas", []):
                registro = registros.get(e.get("clave"))
                fecha = date.fromisoformat(e["fecha"])
                mes = (date.fromisoformat(e["periodo"]) if e.get("periodo") else
                       getattr(registro, "periodo", None) or primer_dia_mes(fecha))
                if mes != periodo:
                    continue
                efecto = Decimal(e.get("efecto", "0"))
                saldo_anterior -= efecto
                if e["tipo"] == "CARGO":
                    cargos += efecto
                else:
                    abonos -= efecto
                entradas.append(dict(e, fecha=fecha, periodo=periodo,
                                     monto=Decimal(e["monto"]), efecto=efecto, pendiente=False))
                referencia_desglosada = True
        for e in [e for c in versiones for e in c.desglose.get("entradas", [])]:
            fila = dict(e, periodo=periodo, fecha=date.fromisoformat(e["fecha"]),
                        monto=Decimal(e["monto"]), efecto=Decimal(e.get("efecto", "0")), pendiente=False)
            if e.get("historica") and e["tipo"] in {"CARGO", "ABONO"}:
                fila["tipo"] = "AJUSTE"
            entradas.append(fila)
    for fila in efectos.values():
        if fila["periodo"] < periodo and not exacto:
            saldo_anterior += fila["efecto"]
            continue
        if fila["periodo"] != periodo and not exacto:
            continue
        if exacto and fila["periodo"] < periodo:
            fila = dict(fila, periodo=periodo, tipo="AJUSTE")
        entradas.append(fila)
        if fila["tipo"] == "CARGO":
            cargos += fila["efecto"]
        elif fila["tipo"] == "ABONO":
            abonos -= fila["efecto"]
        else:
            ajustes += fila["efecto"]
    entradas.sort(key=lambda e: (e["fecha"], e["clave"]))
    pagos = {}
    for fila in entradas:
        if fila["tipo"] != "ABONO":
            continue
        identidad = (fila["fecha"], fila["monto"])
        anterior = pagos.get(identidad)
        if anterior is not None and bool(anterior.get("movimiento_id")) != bool(fila.get("movimiento_id")):
            avisos.append(f"Posible abono duplicado del {fila['fecha']:%d/%m/%Y} por "
                          f"{formatear_monto(fila['monto'], cuenta.moneda)}: revisa el registro manual y la transferencia.")
        pagos[identidad] = fila
    conciliaciones_pendientes = []
    manuales_por_pago, bancos_por_pago = {}, {}
    for abono in cuenta.abonos.filter(vigente=True).select_related("movimiento"):
        clave = (abono.fecha, abono.monto)
        destino = bancos_por_pago if abono.movimiento_id else manuales_por_pago
        destino.setdefault(clave, []).append(abono)
    for clave in manuales_por_pago.keys() & bancos_por_pago.keys():
        fecha, monto = clave
        mes_abonos = max(
            (a.periodo or primer_dia_mes(a.fecha) for a in manuales_por_pago[clave] + bancos_por_pago[clave])
        )
        if mes_abonos != periodo:
            continue
        for bancario in bancos_por_pago[clave]:
            propuesta = cuenta.partidas_revision.filter(
                movimiento_id=bancario.movimiento_id, tipo="ABONO", resuelta_en__isnull=True,
            ).order_by("-created_at", "-pk").first()
            movimiento = bancario.movimiento
            conciliaciones_pendientes.append(SimpleNamespace(
                fecha=fecha, monto=monto, movimiento=movimiento,
                abonos_manuales=manuales_por_pago[clave], ambiguo=len(manuales_por_pago[clave]) != 1,
                propuesta=propuesta,
                url_revision=(
                    f"{reverse('cuentas_por_cobrar')}?gestionar={cuenta.pk}"
                    f"&periodo={propuesta.periodo:%Y-%m}#partida-{propuesta.pk}"
                    if propuesta else ""
                ),
                url_movimientos=(
                    f"{reverse('gastos_movimientos')}?{urlencode({'pendientes': 1, 'q': movimiento.descripcion})}"
                    if movimiento else ""
                ),
            ))
    resumen = SimpleNamespace(periodo=periodo, saldo_anterior=saldo_anterior, cargos=cargos,
                              abonos=abonos, ajustes=ajustes, saldo_final=saldo_anterior + cargos - abonos + ajustes,
                              entradas=entradas, confirmado=confirmado if exacto else None,
                              provisional=not exacto or bool(efectos), avisos=avisos,
                              conciliaciones_pendientes=conciliaciones_pendientes)
    resumen.texto = (confirmado.texto if calculo_pendiente is None and exacto and not efectos and len(versiones) == 1
                     and not referencia_desglosada else _texto(cuenta, resumen))
    return resumen
