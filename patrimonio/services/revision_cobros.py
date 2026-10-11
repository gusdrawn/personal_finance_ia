"""Propuestas de cobro y versiones confirmadas, separadas de los movimientos reales."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from patrimonio.models import (
    AbonoCuentaPorCobrar, CalculoCuentaPorCobrar, CuentaPorCobrar,
    DetalleCuentaPorCobrar, PartidaRevisionCobro,
)
from patrimonio.services.cuentas_por_cobrar import (
    asignar_fuente_a_cuenta, formatear_monto, informacion_fuente,
    primer_dia_mes, proponer_renovacion, ultimo_dia_mes, ventana_fuentes_cobro,
)
from patrimonio.services.preparacion_cobros import (
    captura_preparacion, correcciones_para_revision, fuentes_para_revision, respaldos_cargo, resumen_preparacion,
)

ZERO = Decimal("0")


def registros_referencia(cuenta, detalles=None, abonos=None):
    """Captura valores e identidades; nunca reconstruye una foto desde datos vivos."""
    entradas, contabilizados = [], {}
    detalles = list(cuenta.detalles.filter(vigente=True)) if detalles is None else detalles
    abonos = list(cuenta.abonos.filter(vigente=True)) if abonos is None else abonos
    for tipo, registros in [("CARGO", detalles), ("ABONO", abonos)]:
        for registro in registros:
            if registro.cuenta_id != cuenta.pk:
                raise ValidationError("La referencia contiene registros de otra cuenta.")
            clave = f"{'detalle' if tipo == 'CARGO' else 'abono'}:{registro.pk}"
            importe = registro.monto if tipo == "CARGO" else -registro.monto
            contabilizados[clave] = str(importe)
            entrada = {"tipo": tipo, "clave": clave, "descripcion": registro.descripcion,
                       "fecha": registro.fecha.isoformat(), "monto": str(registro.monto),
                       "decision": "REFERENCIA", "efecto": str(importe)}
            entrada["periodo"] = (registro.periodo or primer_dia_mes(registro.fecha)).isoformat()
            if registro.movimiento_id:
                entrada["movimiento_id"] = registro.movimiento_id
                contabilizados[f"movimiento:{registro.movimiento_id}"] = "0"
            if tipo == "CARGO" and registro.evento_externo_id:
                entrada["evento_externo_id"] = registro.evento_externo_id
                contabilizados[f"neat:{registro.evento_externo_id}"] = "0"
            entradas.append(entrada)
    return {"entradas": entradas, "contabilizados": contabilizados}


@transaction.atomic
def registrar_referencia(cuenta, periodo, fecha, saldo_anterior=ZERO, *, detalles=None,
                         abonos=None, texto="", usuario=True, cargos=None, monto_esperado=None):
    cuenta = CuentaPorCobrar.objects.select_for_update().get(pk=cuenta.pk)
    desglose = registros_referencia(cuenta, detalles, abonos)
    entradas = desglose["entradas"]
    total_cargos = sum((Decimal(e["monto"]) for e in entradas if e["tipo"] == "CARGO"), ZERO)
    total_abonos = sum((Decimal(e["monto"]) for e in entradas if e["tipo"] == "ABONO"), ZERO)
    total_cargos = total_cargos if cargos is None else Decimal(cargos)
    saldo_anterior = Decimal(saldo_anterior)
    saldo = saldo_anterior + total_cargos - total_abonos
    if monto_esperado is not None and saldo != Decimal(monto_esperado):
        raise ValidationError("El desglose no coincide con el saldo de referencia.")
    periodo = primer_dia_mes(periodo)
    existente = cuenta.calculos.filter(estado="REFERENCIA", referencia_usuario=usuario,
                                       fecha_calculo=fecha, periodo=periodo, saldo_final=max(saldo, ZERO)).first()
    if existente:
        return existente
    revision = (cuenta.calculos.filter(periodo=periodo).aggregate(n=Max("revision"))["n"] or 0) + 1
    return CalculoCuentaPorCobrar.objects.create(
        cuenta=cuenta, periodo=periodo, revision=revision, estado="REFERENCIA",
        referencia_usuario=usuario, fecha_calculo=fecha, saldo_anterior=saldo_anterior,
        cargos=total_cargos, abonos=total_abonos, saldo_final=max(saldo, ZERO),
        desglose=desglose, texto=texto or _texto(cuenta, saldo_anterior, entradas, max(saldo, ZERO)),
    )


def asegurar_referencia_legado(cuenta):
    if not cuenta.calculos.filter(estado="REFERENCIA", referencia_usuario=False).exists():
        periodo = cuenta.detalles.aggregate(n=Max("periodo"))["n"] or primer_dia_mes(timezone.localdate())
        registrar_referencia(cuenta, periodo, timezone.localdate(), usuario=False)


def calculo_anterior(cuenta, periodo, referencia_id=None):
    ultimo = cuenta.calculos.filter(estado="CONFIRMADO").first()
    if ultimo:
        if ultimo.periodo > periodo:
            raise ValidationError("Este mes ya está cerrado. Registra el ajuste en el mes abierto.")
        if referencia_id == "sin_saldo":
            raise ValidationError("El saldo confirmado se conserva como punto de partida.")
        return ultimo
    if referencia_id == "sin_saldo":
        return None
    referencias = cuenta.calculos.filter(estado="REFERENCIA", periodo__lte=periodo)
    if referencia_id:
        try:
            referencia_id = int(referencia_id)
        except (ValueError, TypeError) as error:
            raise ValidationError("Selecciona una referencia válida de esta cuenta.") from error
        anterior = referencias.filter(pk=referencia_id).first()
        if anterior is None:
            raise ValidationError("La referencia debe pertenecer a esta cuenta y no ser de un mes posterior.")
        return anterior
    # Una referencia aportada es una alternativa al saldo registrado, no un
    # requisito para continuar usando la cuenta.
    return referencias.order_by("referencia_usuario", "-fecha_calculo", "-revision").first()


def _contabilizados(anterior):
    return dict((anterior.desglose or {}).get("contabilizados", {})) if anterior else {}


def periodo_correccion(cuenta, periodo, *, reconstruir=False):
    """Una edición histórica se revisa desde el saldo vigente, nunca antes de su base."""
    calculos = list(cuenta.calculos.all())
    base = next((c for c in calculos if c.estado == "CONFIRMADO"), None)
    if base is None and not reconstruir:
        base = next((c for c in calculos if c.estado == "REFERENCIA" and not c.referencia_usuario), None)
    # Antes de guardar la primera propuesta aún puede faltar la referencia de
    # legado. Consultar el formulario no debe crearla ni cambiar ningún saldo.
    limite = base.periodo if base else None
    if limite is None and not reconstruir:
        limite = cuenta.detalles.aggregate(n=Max("periodo"))["n"]
    return max(primer_dia_mes(periodo), limite) if limite else primer_dia_mes(periodo)


def _validar_fuente(cuenta, tipo, fuente):
    if fuente.user_id != cuenta.user_id or fuente.moneda != cuenta.moneda:
        raise ValidationError("La fuente y la cuenta deben pertenecer al mismo usuario y moneda.")
    if getattr(fuente, "estado", "") in {"DESCARTADO_CIERRE", "FALLIDO", "REVERSADO"}:
        raise ValidationError("La fuente fue descartada o falló.")
    if hasattr(fuente, "producto_id"):
        if fuente.producto.user_id != cuenta.user_id:
            raise ValidationError("El producto pertenece a otro usuario.")
        if (tipo == "ABONO") == fuente.es_cargo:
            raise ValidationError("El signo del movimiento no corresponde al cargo o abono.")
    elif tipo == "ABONO":
        raise ValidationError("Un abono requiere una transferencia entrante o registro manual.")


@transaction.atomic
def proponer_partida(cuenta, tipo, *, periodo=None, fuente=None, detalle=None, abono=None,
                     monto=None, fecha=None, descripcion="", motivo="", provisional=False,
                     registrada_por_usuario=False):
    cuenta = CuentaPorCobrar.objects.select_for_update().get(pk=cuenta.pk)
    if not cuenta.activo:
        raise ValidationError("La cuenta está archivada; reactívala para gestionar movimientos.")
    asegurar_referencia_legado(cuenta)
    if periodo is None and fuente is not None:
        fecha_fuente = informacion_fuente(fuente)["fecha"]
        periodo = getattr(fuente, "periodo_factura", None) or getattr(fuente, "periodo", None) or fecha_fuente
    periodo = primer_dia_mes(periodo or timezone.localdate())
    periodo_mensual = periodo if registrada_por_usuario else None
    if registrada_por_usuario or fuente is not None:
        confirmado = cuenta.calculos.filter(estado="CONFIRMADO").first()
        if confirmado:
            periodo = max(periodo, confirmado.periodo)
    if tipo == "EDITAR":
        periodo = periodo_correccion(cuenta, periodo, reconstruir=registrada_por_usuario)
    calculo_anterior(cuenta, periodo)
    movimiento = evento = None
    if fuente is not None:
        _validar_fuente(cuenta, "ABONO" if tipo == "ABONO" else "CARGO", fuente)
        from patrimonio.services.cuentas_por_cobrar import validar_fuente_no_solapada
        if tipo != "ABONO":
            try:
                validar_fuente_no_solapada(fuente, cuenta, detalle.pk if detalle else None)
            except ValueError as error:
                # La cartola puede traer un pago ya registrado por Neat. Se
                # permite revisarlo como respaldo, pero nunca aplicarlo dos veces.
                if tipo != "CARGO" or not respaldos_cargo(cuenta, fuente):
                    raise ValidationError(str(error)) from error
            vinculos = DetalleCuentaPorCobrar.objects.filter(vigente=True)
        else:
            vinculos = AbonoCuentaPorCobrar.objects.filter(vigente=True)
        filtro = {"movimiento": fuente} if hasattr(fuente, "producto_id") else {"evento_externo": fuente}
        if PartidaRevisionCobro.objects.filter(
            cuenta__user_id=cuenta.user_id, resuelta_en__isnull=True, **filtro,
        ).exclude(cuenta=cuenta).exists():
            raise ValidationError("Esta fuente ya está propuesta para otra cuenta. Revisa primero esa asignación.")
        if vinculos.filter(**filtro).exclude(cuenta=cuenta).exists():
            raise ValidationError("Esta fuente ya está vinculada a otra cuenta. Revisa primero su retiro.")
        if tipo == "EDITAR" and vinculos.filter(**filtro).exclude(pk=detalle.pk).exists():
            raise ValidationError("Esta fuente ya respalda otro cargo. Elige una fuente sin aplicar.")
        info = informacion_fuente(fuente)
        monto = abs(fuente.monto_clp) if tipo == "ABONO" and cuenta.moneda == "CLP" else info["monto"]
        fecha = info["fecha"]
        if fecha is None or monto <= 0:
            raise ValidationError("La fuente no tiene fecha e importe válidos.")
        descripcion = descripcion or info["descripcion"]
        if hasattr(fuente, "producto_id"):
            movimiento = fuente
            if tipo == "ABONO" and abono is None:
                abono = cuenta.abonos.filter(movimiento=fuente, vigente=True).first()
            elif tipo == "CARGO" and detalle is None:
                detalle = cuenta.detalles.filter(movimiento=fuente, vigente=True).first()
        else:
            evento = fuente
            if tipo == "CARGO" and detalle is None:
                detalle = cuenta.detalles.filter(evento_externo=fuente, vigente=True).first()
    registro = detalle or abono
    if registro and not motivo and tipo in {"EDITAR", "RETIRAR_CARGO", "RETIRAR_ABONO"}:
        accion = "Corrección" if tipo == "EDITAR" else "Retiro"
        motivo = f"{accion} de registro del {registro.fecha:%d/%m/%Y}"
    if tipo.startswith("RETIRAR"):
        monto, fecha, descripcion = registro.monto, registro.fecha, registro.descripcion
    clave = f"{tipo.lower()}:{detalle.pk}" if detalle and tipo != "ABONO" else f"{tipo.lower()}:{abono.pk}" if abono else (
        f"movimiento:{movimiento.pk}" if movimiento else f"neat:{evento.pk}" if evento else f"manual:{uuid4().hex}"
    )
    if tipo == "CARGO" and detalle:
        clave = f"detalle:{detalle.pk}"
    if tipo == "ABONO" and abono:
        clave = f"abono:{abono.pk}"
    valores = dict(periodo=periodo, periodo_mensual=periodo_mensual,
                   tipo=tipo, movimiento=movimiento, evento_externo=evento,
                   detalle=detalle, abono=abono, monto=Decimal(monto or 0),
                   fecha=fecha or timezone.localdate(), descripcion=descripcion or "Abono",
                   motivo=motivo, provisional=provisional, registrada_por_usuario=registrada_por_usuario)
    partida = cuenta.partidas_revision.filter(clave=clave, resuelta_en__isnull=True).first()
    if partida is None:
        partida = PartidaRevisionCobro(cuenta=cuenta, clave=clave, **valores)
    else:
        for campo, valor in valores.items():
            setattr(partida, campo, valor)
        partida.decision = "PENDIENTE"
    if tipo == "ABONO":
        coincidencias = cuenta.abonos.filter(vigente=True, movimiento__isnull=True,
                                             fecha=partida.fecha, monto=partida.monto)
        if abono:
            coincidencias = coincidencias.exclude(pk=abono.pk)
        candidatos = list(coincidencias[:2])
        partida.conciliar_con = candidatos[0] if len(candidatos) == 1 else None
    partida.save()
    return partida


def _snapshot_partida(partida):
    datos = {"id": partida.pk, "clave": partida.clave, "tipo": partida.tipo,
            "descripcion": partida.descripcion, "fecha": partida.fecha.isoformat(),
            "periodo": partida.periodo.isoformat(), "monto": str(partida.monto),
            "periodo_mensual": partida.periodo_mensual.isoformat() if partida.periodo_mensual else None,
            "decision": partida.decision, "motivo": partida.motivo,
            "detalle_id": partida.detalle_id, "abono_id": partida.abono_id,
            "movimiento_id": partida.movimiento_id, "evento_externo_id": partida.evento_externo_id,
            "conciliar_con_id": partida.conciliar_con_id, "provisional": partida.provisional,
            "registrada_por_usuario": partida.registrada_por_usuario}
    if partida.detalle_id:
        datos["periodo_original"] = partida.detalle.periodo.isoformat()
    fuente = partida.movimiento or partida.evento_externo
    if partida.tipo == "CARGO" and fuente:
        datos["respalda_detalle_ids"] = [d.pk for d in respaldos_cargo(partida.cuenta, fuente)]
    return datos


def _fuente_actual(objeto):
    if objeto is None:
        return None
    campos = ["id", "user_id", "cuenta_id", "producto_id", "fecha", "fecha_cargo", "fecha_evento",
              "periodo", "monto", "monto_clp", "monto_neto", "monto_bruto", "moneda",
              "descripcion", "estado", "vigente", "updated_at", "movimiento_id", "evento_externo_id"]
    datos = {c: str(getattr(objeto, c)) for c in campos if hasattr(objeto, c)}
    for nombre in ["conciliaciones", "conciliaciones_externas"]:
        relacion = getattr(objeto, nombre, None)
        if relacion is not None:
            datos[nombre] = list(relacion.order_by("pk").values("pk", "evento_id", "movimiento_id", "rol", "monto_asignado"))
            for vinculo in datos[nombre]:
                vinculo["monto_asignado"] = str(vinculo["monto_asignado"])
    return datos


def _partidas(cuenta, periodo):
    return list(cuenta.partidas_revision.filter(resuelta_en__isnull=True, periodo__lte=periodo)
                .select_related("movimiento__producto", "evento_externo", "detalle", "abono", "conciliar_con"))


def huella_propuesta(cuenta, periodo, anterior, partidas):
    ultimo = cuenta.calculos.filter(estado="CONFIRMADO").first()
    datos = {"cuenta": cuenta.pk, "moneda": cuenta.moneda, "activa": cuenta.activo,
             "periodo": str(periodo), "anterior": anterior.pk if anterior else None,
             "ultimo": ultimo.pk if ultimo else None, "partidas": []}
    # Una cartola nueva o un cruce Neat posterior también cambia la evidencia.
    from banca.models import ImportacionEstadoCuenta
    datos["documentos"] = list(ImportacionEstadoCuenta.objects.filter(
        user_id=cuenta.user_id, moneda=cuenta.moneda, tipo_documento="ESTADO_CUENTA",
    ).order_by("pk").values("pk", "estado", "vigente", "producto_id", "fecha_estado", "periodo_inicio", "periodo_fin"))
    for partida in partidas:
        datos["partidas"].append({"partida": _snapshot_partida(partida), "updated_at": str(partida.updated_at),
                                   "fuentes": [_fuente_actual(f) for f in [partida.movimiento, partida.evento_externo,
                                                                           partida.detalle, partida.abono, partida.conciliar_con]]})
    return hashlib.sha256(json.dumps(datos, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _efecto(partida, anterior, periodo):
    fuente = partida.movimiento or partida.evento_externo
    respaldos = respaldos_cargo(partida.cuenta, fuente) if partida.tipo == "CARGO" and fuente else []
    incluidos = _contabilizados(anterior)
    if partida.decision in {"PENDIENTE", "EXCLUIR", "RESPALDO"}:
        if partida.decision == "RESPALDO" and partida.tipo not in {"CARGO", "ABONO"}:
            raise ValidationError("Sólo un cargo o abono puede servir como respaldo.")
        if partida.decision == "RESPALDO" and respaldos and any(
            f"detalle:{d.pk}" not in incluidos for d in respaldos
        ):
            raise ValidationError("El cargo que respalda esta cartola todavía no está incluido en el saldo anterior.")
        return ZERO
    contabilizados = _contabilizados(anterior)
    if partida.decision == "CONCILIAR":
        abono = partida.conciliar_con
        if partida.tipo != "ABONO" or abono is None or not abono.vigente:
            raise ValidationError("Elige un abono vigente de esta cuenta para conciliar.")
        if abono.cuenta_id != partida.cuenta_id or abono.monto != partida.monto or abono.fecha != partida.fecha:
            raise ValidationError("El abono a conciliar debe coincidir en cuenta, fecha e importe.")
        if f"abono:{abono.pk}" not in contabilizados:
            raise ValidationError("El abono a conciliar todavía no está incluido en el saldo anterior.")
        if partida.movimiento_id is None:
            raise ValidationError("Selecciona el movimiento bancario que respalda este abono.")
        if abono.movimiento_id and abono.movimiento_id != partida.movimiento_id:
            raise ValidationError("Este abono ya está conciliado con otra transferencia.")
        claves = {f"abono:{abono.pk}"}
        if partida.abono_id:
            claves.add(f"abono:{partida.abono_id}")
        incluido = sum((Decimal(contabilizados.get(clave, "0")) for clave in claves), ZERO)
        # Si ambos registros ya se habían descontado, corrige el duplicado en
        # el mes abierto. Si sólo estaba el manual, el efecto es cero.
        return -partida.monto - incluido
    if partida.decision != "APLICAR":
        raise ValidationError("Decisión de revisión inválida.")
    if respaldos:
        raise ValidationError("Este pago ya está registrado mediante Neat o el banco. Vincula la cartola como respaldo, sin crear otro cargo.")
    if partida.tipo == "AJUSTE":
        return partida.monto
    if partida.tipo.startswith("RETIRAR"):
        clave = f"detalle:{partida.detalle_id}" if partida.detalle_id else f"abono:{partida.abono_id}"
        return -Decimal(contabilizados.get(clave, "0"))
    if partida.tipo == "EDITAR":
        minimo = periodo_correccion(partida.cuenta, periodo, reconstruir=partida.registrada_por_usuario)
        if minimo > periodo:
            raise ValidationError(f"Esta corrección debe revisarse en {minimo:%m/%Y} o después, desde el saldo vigente.")
        return partida.monto - Decimal(contabilizados.get(f"detalle:{partida.detalle_id}", "0"))
    claves = [partida.clave]
    if partida.movimiento_id:
        claves.append(f"movimiento:{partida.movimiento_id}")
    if partida.evento_externo_id:
        claves.append(f"neat:{partida.evento_externo_id}")
    if any(clave in contabilizados for clave in claves):
        raise ValidationError("Esta fuente ya está incluida. Vincúlala como respaldo o concíliala.")
    return -partida.monto if partida.tipo == "ABONO" else partida.monto


def _texto(cuenta, saldo_anterior, entradas, saldo):
    lineas = [f"Detalle pendiente: {cuenta.nombre}"]
    if cuenta.descripcion:
        lineas.append(cuenta.descripcion)
    if saldo_anterior:
        lineas.append(f"- Saldo pendiente anterior: {formatear_monto(saldo_anterior, cuenta.moneda)}")
    for entrada in entradas:
        efecto = Decimal(entrada.get("efecto", "0"))
        if not efecto:
            continue
        etiqueta = "Abono" if entrada["tipo"] == "ABONO" and not entrada.get("historica") else "Ajuste" if entrada.get("historica") or entrada["tipo"] not in {"CARGO", "ABONO"} else ""
        importe = ("-" if efecto < 0 else "") + formatear_monto(abs(efecto), cuenta.moneda)
        lineas.append(f"- {etiqueta + ' · ' if etiqueta else ''}{entrada['descripcion']}: {importe}")
    lineas.append(f"Total pendiente: {formatear_monto(saldo, cuenta.moneda)}")
    return "\n".join(lineas)


def _recalcular(calculo, partidas):
    cargos = abonos = ajustes = ZERO
    entradas = []
    inicio, _ = ventana_fuentes_cobro(calculo.periodo)
    for partida in partidas:
        efecto = _efecto(partida, calculo.anterior, calculo.periodo)
        entrada = _snapshot_partida(partida)
        entrada["efecto"] = str(efecto)
        entrada["historica"] = partida.fecha < inicio or bool(partida.detalle_id and partida.detalle.periodo < calculo.periodo)
        if partida.tipo in {"EDITAR", "RETIRAR_CARGO", "RETIRAR_ABONO", "AJUSTE"} or partida.decision == "CONCILIAR" or entrada["historica"]:
            ajustes += efecto
        elif efecto > 0:
            cargos += efecto
        else:
            abonos -= efecto
        entradas.append(entrada)
    calculo.saldo_anterior = calculo.anterior.saldo_final if calculo.anterior else ZERO
    calculo.cargos, calculo.abonos, calculo.ajustes = cargos, abonos, ajustes
    calculo.saldo_final = calculo.saldo_anterior + cargos - abonos + ajustes
    preparacion = calculo.desglose.get("preparacion", {})
    calculo.desglose = {"entradas": entradas, "contabilizados": _contabilizados(calculo.anterior), "preparacion": preparacion}
    calculo.texto = _texto(calculo.cuenta, calculo.saldo_anterior, entradas, calculo.saldo_final)
    calculo.huella = huella_propuesta(calculo.cuenta, calculo.periodo, calculo.anterior, partidas)
    return calculo


@transaction.atomic
def preparar_calculo(cuenta, periodo, referencia_id=None, *, trasladar_ids=None, incluir_registros_guardados=False):
    cuenta = CuentaPorCobrar.objects.select_for_update().get(pk=cuenta.pk)
    if not cuenta.activo:
        raise ValidationError("La cuenta está archivada.")
    asegurar_referencia_legado(cuenta)
    periodo = primer_dia_mes(periodo)
    anterior = calculo_anterior(cuenta, periodo, referencia_id)
    calculo = cuenta.calculos.select_for_update().filter(estado="BORRADOR", periodo=periodo).first()
    if calculo and calculo.anterior_id != (anterior.pk if anterior else None):
        for partida in _partidas(cuenta, periodo):
            if partida.decision != "PENDIENTE":
                partida.decision = "PENDIENTE"
                partida.save()
    contabilizados = _contabilizados(anterior)
    # Sólo una selección explícita traslada una asociación que quedó en otro
    # mes; consultar o importar archivos nunca cambia el período de aplicación.
    try:
        seleccion = {str(int(pk)) for pk in (trasladar_ids or [])}
    except (ValueError, TypeError) as error:
        raise ValidationError("Selecciona partidas válidas para esta revisión.") from error
    trasladables = list(cuenta.partidas_revision.select_for_update().filter(
        pk__in=seleccion, resuelta_en__isnull=True,
    ))
    if {str(p.pk) for p in trasladables} != seleccion:
        raise ValidationError("La selección contiene partidas que no pertenecen a esta revisión.")
    for partida in trasladables:
        if partida.fecha > ultimo_dia_mes(periodo):
            raise ValidationError("Un movimiento posterior no se puede aplicar a este mes.")
        if partida.periodo != periodo:
            partida.periodo, partida.decision = periodo, "PENDIENTE"
            partida.save()
    for partida in _partidas(cuenta, periodo):
        fuente = partida.movimiento or partida.evento_externo
        if fuente is None or partida.tipo.startswith("RETIRAR"):
            continue
        _validar_fuente(cuenta, "ABONO" if partida.tipo == "ABONO" else "CARGO", fuente)
        info = informacion_fuente(fuente)
        monto = abs(fuente.monto_clp) if partida.tipo == "ABONO" and cuenta.moneda == "CLP" else info["monto"]
        if monto != partida.monto or info["fecha"] != partida.fecha:
            partida.monto, partida.fecha = monto, info["fecha"]
            partida.decision = "PENDIENTE"
            partida.conciliar_con = None
            partida.save()
    for tipo, registros, prefijo in [
        ("CARGO", cuenta.detalles.filter(vigente=True, periodo__lte=periodo), "detalle"),
        ("ABONO", cuenta.abonos.filter(vigente=True).filter(
            Q(periodo__lte=periodo) | Q(periodo__isnull=True, fecha__lte=ultimo_dia_mes(periodo))
        ), "abono"),
    ]:
        for registro in registros:
            clave = f"{prefijo}:{registro.pk}"
            if clave in contabilizados or cuenta.partidas_revision.filter(clave=clave).exists():
                continue
            if tipo == "CARGO" and cuenta.partidas_revision.filter(
                detalle=registro, tipo="EDITAR", resuelta_en__isnull=True,
            ).exists():
                # La corrección ya representa este registro. Con una base sin
                # saldo inicial también debe poder aplicarse como un solo cargo.
                continue
            fuente = registro.movimiento
            if tipo == "CARGO" and registro.evento_externo_id:
                fuente = registro.evento_externo
            proponer_partida(cuenta, tipo, periodo=periodo, fuente=fuente,
                              detalle=registro if tipo == "CARGO" else None,
                              abono=registro if tipo == "ABONO" else None,
                              monto=registro.monto, fecha=registro.fecha, descripcion=registro.descripcion)
    renovacion = proponer_renovacion(cuenta, periodo)
    for propuesta in renovacion["propuestas"]:
        fuente = propuesta["fuente"]
        clave = f"movimiento:{fuente.pk}" if hasattr(fuente, "producto_id") else f"neat:{fuente.pk}"
        if clave in contabilizados or cuenta.partidas_revision.filter(clave=clave).exists():
            continue
        proponer_partida(cuenta, "CARGO", periodo=periodo, fuente=fuente, descripcion=propuesta["descripcion"])
    for tipo, fuente in fuentes_para_revision(cuenta, periodo, anterior):
        clave = f"movimiento:{fuente.pk}"
        if clave in contabilizados or cuenta.partidas_revision.filter(movimiento=fuente).exists():
            continue
        proponer_partida(cuenta, tipo, periodo=periodo, fuente=fuente)
    for detalle, fuente in correcciones_para_revision(cuenta, anterior):
        if cuenta.partidas_revision.filter(detalle=detalle, tipo="EDITAR").exists():
            continue
        proponer_partida(cuenta, "EDITAR", periodo=periodo, fuente=fuente, detalle=detalle,
                         descripcion=detalle.descripcion,
                         motivo=f"Fuente Neat de otro período; posible pago de {detalle.periodo:%m/%Y}. Revisar vinculación.")
    if calculo is None:
        revision = (cuenta.calculos.filter(periodo=periodo).aggregate(n=Max("revision"))["n"] or 0) + 1
        calculo = CalculoCuentaPorCobrar(cuenta=cuenta, periodo=periodo, revision=revision,
                                         fecha_calculo=timezone.localdate())
    calculo.anterior = anterior
    calculo.desglose["preparacion"] = captura_preparacion(resumen_preparacion(cuenta, periodo, anterior))
    if incluir_registros_guardados:
        for partida in _partidas(cuenta, periodo):
            if (partida.registrada_por_usuario and partida.decision == "PENDIENTE" and not partida.conciliar_con_id
                    and not partida.movimiento_id and not partida.evento_externo_id):
                partida.decision = "APLICAR"
                try:
                    _efecto(partida, anterior, periodo)
                except ValidationError:
                    # Un respaldo ya incluido o una fuente solapada necesita
                    # su decisión específica, aunque la asociación sea manual.
                    partida.decision = "PENDIENTE"
                    continue
                partida.save()
    _recalcular(calculo, _partidas(cuenta, periodo)).save()
    return calculo


@transaction.atomic
def guardar_decisiones(calculo, decisiones):
    cuenta = CuentaPorCobrar.objects.select_for_update().get(pk=calculo.cuenta_id)
    calculo = cuenta.calculos.select_for_update().get(pk=calculo.pk)
    if calculo.estado != "BORRADOR":
        raise ValidationError("Este cálculo ya fue confirmado.")
    calculo_anterior(cuenta, calculo.periodo)
    partidas = _partidas(cuenta, calculo.periodo)
    for partida in partidas:
        seleccion = decisiones.get(str(partida.pk))
        if seleccion is None:
            continue
        fuente = partida.movimiento or partida.evento_externo
        if fuente and not partida.tipo.startswith("RETIRAR"):
            info = informacion_fuente(fuente)
            monto = abs(fuente.monto_clp) if partida.tipo == "ABONO" and cuenta.moneda == "CLP" else info["monto"]
            if partida.monto != monto or partida.fecha != info["fecha"]:
                raise ValidationError("La fuente cambió. Actualiza la propuesta antes de guardar las decisiones.")
        partida.decision = seleccion["decision"]
        if "conciliar_con" in seleccion:
            partida.conciliar_con = cuenta.abonos.filter(pk=seleccion["conciliar_con"], vigente=True).first() if seleccion["conciliar_con"] else None
        if "motivo" in seleccion:
            partida.motivo = seleccion["motivo"]
        if "periodo" in seleccion:
            nuevo_periodo = primer_dia_mes(seleccion["periodo"])
            calculo_anterior(cuenta, nuevo_periodo)
            if nuevo_periodo < calculo.periodo:
                raise ValidationError("Las correcciones se aplican al mes abierto o a un mes futuro.")
            partida.periodo = nuevo_periodo
        _efecto(partida, calculo.anterior, calculo.periodo)
        partida.save()
    return _recalcular(calculo, _partidas(cuenta, calculo.periodo)).save() or calculo


def _marcar_abono(movimiento):
    movimiento.naturaleza = "TRANSFERENCIA"
    movimiento.naturaleza_es_manual = True
    movimiento.por_revisar = False
    movimiento.categoria = None
    movimiento.categoria_es_manual = False
    movimiento.save(update_fields=["naturaleza", "naturaleza_es_manual", "por_revisar", "categoria", "categoria_es_manual", "updated_at"])


def _materializar(partida, contabilizados):
    """Actualiza el respaldo contable dentro de la misma transacción de confirmación."""
    decision = partida.decision
    if decision == "EXCLUIR":
        if partida.tipo == "EDITAR":
            return
        registro = partida.detalle if partida.tipo in {"CARGO", "EDITAR", "RETIRAR_CARGO"} else partida.abono
        clave = f"detalle:{registro.pk}" if registro and partida.detalle_id and partida.tipo != "ABONO" else f"abono:{registro.pk}" if registro else ""
        if registro and clave not in contabilizados:
            registro.vigente = False
            registro.save(update_fields=["vigente", "updated_at"])
        return
    if partida.tipo.startswith("RETIRAR"):
        registro = partida.detalle or partida.abono
        registro.vigente = False
        registro.save(update_fields=["vigente", "updated_at"])
        clave = f"detalle:{registro.pk}" if partida.detalle_id else f"abono:{registro.pk}"
        contabilizados[clave] = "0"
        return
    if partida.tipo == "AJUSTE":
        return
    if partida.tipo in {"CARGO", "EDITAR"}:
        fuente = partida.movimiento or partida.evento_externo
        detalle = partida.detalle
        periodo_original = detalle.periodo if detalle and partida.tipo == "EDITAR" else partida.periodo_vista
        respaldos = respaldos_cargo(partida.cuenta, fuente) if fuente and partida.tipo == "CARGO" else []
        if decision == "RESPALDO" and respaldos:
            # Conserva el desglose original, incluso si un cargo de tarjeta
            # agrupa varios pagos Neat. La relación queda en la decisión y
            # en ConciliacionEventoExterno, sin una segunda obligación.
            if partida.movimiento_id:
                contabilizados[f"movimiento:{partida.movimiento_id}"] = "0"
            if partida.evento_externo_id:
                contabilizados[f"neat:{partida.evento_externo_id}"] = "0"
            return
        if fuente:
            try:
                detalle = asignar_fuente_a_cuenta(fuente, partida.cuenta, detalle_base=detalle)
            except ValueError as error:
                raise ValidationError(str(error)) from error
            detalle.descripcion = partida.descripcion
            detalle.periodo = periodo_original
            detalle.save(update_fields=["descripcion", "periodo", "updated_at"])
        else:
            detalle = detalle or DetalleCuentaPorCobrar(cuenta=partida.cuenta)
            detalle.descripcion, detalle.periodo, detalle.fecha = partida.descripcion, periodo_original, partida.fecha
            detalle.monto, detalle.provisional, detalle.vigente = partida.monto, partida.provisional, True
            detalle.monto_manual, detalle.origen = True, "MANUAL"
            detalle.movimiento = detalle.evento_externo = None
            detalle.save()
        partida.detalle = detalle
        clave = f"detalle:{detalle.pk}"
        contabilizados[clave] = str(Decimal(contabilizados.get(clave, partida.monto)) if decision == "RESPALDO" else partida.monto)
    else:
        movimiento = partida.movimiento
        abono = partida.abono
        if movimiento:
            existente = AbonoCuentaPorCobrar.objects.filter(movimiento=movimiento).first()
            if existente and existente.cuenta_id != partida.cuenta_id:
                raise ValidationError("La transferencia ya pertenece a otra cuenta.")
            abono = existente or abono
        if decision == "CONCILIAR":
            manual = partida.conciliar_con
            if abono and abono.pk != manual.pk:
                manual.vigente = False
                manual.conciliado_con = abono
                manual.save(update_fields=["vigente", "conciliado_con", "updated_at"])
                contabilizados[f"abono:{manual.pk}"] = "0"
            else:
                abono = manual
        abono = abono or AbonoCuentaPorCobrar(cuenta=partida.cuenta)
        abono.fecha, abono.monto, abono.descripcion = partida.fecha, partida.monto, partida.descripcion
        abono.periodo = partida.periodo_vista
        if partida.detalle_id:
            abono.detalle = partida.detalle
        abono.movimiento, abono.vigente = movimiento, True
        abono.save()
        partida.abono = abono
        clave = f"abono:{abono.pk}"
        contabilizados[clave] = str(Decimal(contabilizados.get(clave, -partida.monto)) if decision == "RESPALDO" else -partida.monto)
        if movimiento:
            _marcar_abono(movimiento)
    if partida.movimiento_id:
        contabilizados[f"movimiento:{partida.movimiento_id}"] = "0"
    if partida.evento_externo_id:
        contabilizados[f"neat:{partida.evento_externo_id}"] = "0"


@transaction.atomic
def confirmar_calculo(calculo, huella):
    cuenta = CuentaPorCobrar.objects.select_for_update().get(pk=calculo.cuenta_id)
    calculo = cuenta.calculos.select_for_update().select_related("anterior").get(pk=calculo.pk)
    if calculo.estado == "CONFIRMADO":
        return calculo
    if calculo.estado != "BORRADOR" or not cuenta.activo:
        raise ValidationError("El cálculo no es un borrador de una cuenta activa.")
    ultimo = calculo_anterior(cuenta, calculo.periodo)
    if ultimo and ultimo.estado == "CONFIRMADO" and ultimo.pk != calculo.anterior_id:
        raise ValidationError("Cambió el saldo anterior. Prepara nuevamente el cálculo.")
    partidas = _partidas(cuenta, calculo.periodo)
    # Leer las fuentes después de adquirir sus bloqueos permite detectar
    # también cambios concurrentes a la importación o a la conciliación.
    from banca.models import EventoFinancieroExterno, MovimientoBancario
    list(MovimientoBancario.objects.select_for_update().filter(pk__in=[p.movimiento_id for p in partidas if p.movimiento_id]).order_by("pk"))
    list(EventoFinancieroExterno.objects.select_for_update().filter(pk__in=[p.evento_externo_id for p in partidas if p.evento_externo_id]).order_by("pk"))
    list(DetalleCuentaPorCobrar.objects.select_for_update().filter(pk__in=[p.detalle_id for p in partidas if p.detalle_id]).order_by("pk"))
    list(AbonoCuentaPorCobrar.objects.select_for_update().filter(pk__in={pk for p in partidas for pk in [p.abono_id, p.conciliar_con_id] if pk}).order_by("pk"))
    partidas = _partidas(cuenta, calculo.periodo)
    actual = huella_propuesta(cuenta, calculo.periodo, calculo.anterior, partidas)
    if not huella or huella != calculo.huella or huella != actual:
        raise ValidationError("Los datos cambiaron desde la propuesta. Prepara nuevamente el cálculo y revísalo.")
    seleccionadas = [p for p in partidas if p.decision != "PENDIENTE"]
    _recalcular(calculo, partidas)
    historicas = {entrada["id"]: entrada["historica"] for entrada in calculo.desglose["entradas"]}
    if calculo.saldo_final < 0:
        raise ValidationError(f"Los abonos exceden el saldo en {formatear_monto(-calculo.saldo_final, cuenta.moneda)}. Revisa la aplicación de los pagos.")
    contabilizados = _contabilizados(calculo.anterior)
    entradas = []
    fuentes_usadas = set()
    abonos_conciliados = set()
    registros_usados = set()
    for partida in seleccionadas:
        partida.full_clean()
        clave_registro = ("detalle", partida.detalle_id) if partida.detalle_id and partida.tipo != "ABONO" else ("abono", partida.abono_id) if partida.abono_id else None
        if clave_registro:
            if clave_registro in registros_usados:
                raise ValidationError("El mismo registro tiene dos decisiones. Conserva una y deja la otra pendiente.")
            registros_usados.add(clave_registro)
        if partida.decision == "CONCILIAR":
            if partida.conciliar_con_id in abonos_conciliados:
                raise ValidationError("Un abono manual no puede conciliar dos transferencias.")
            abonos_conciliados.add(partida.conciliar_con_id)
        fuente = partida.movimiento or partida.evento_externo
        if fuente:
            _validar_fuente(cuenta, "ABONO" if partida.tipo == "ABONO" else "CARGO", fuente)
            clave_fuente = ("movimiento" if partida.movimiento_id else "neat", fuente.pk)
            if clave_fuente in fuentes_usadas:
                raise ValidationError("La misma fuente aparece más de una vez en la propuesta.")
            fuentes_usadas.add(clave_fuente)
            if partida.tipo in {"CARGO", "EDITAR"}:
                vinculos = DetalleCuentaPorCobrar.objects.filter(vigente=True)
                filtro = {"movimiento": fuente} if partida.movimiento_id else {"evento_externo": fuente}
                if vinculos.filter(**filtro).exclude(cuenta=cuenta).exists():
                    raise ValidationError("La fuente ya está confirmada en otra cuenta.")
        efecto = _efecto(partida, calculo.anterior, calculo.periodo)
        registro_original = ({"monto": str(partida.detalle.monto),
                              "descripcion": partida.detalle.descripcion,
                              "fecha": partida.detalle.fecha.isoformat()}
                             if partida.tipo == "EDITAR" and partida.detalle_id else None)
        _materializar(partida, contabilizados)
        entrada = _snapshot_partida(partida)
        if registro_original is not None:
            entrada["registro_original"] = registro_original
        entrada["efecto"] = str(efecto)
        entrada["historica"] = historicas[partida.pk]
        entradas.append(entrada)
        partida.resuelta_en = calculo
        partida.save()
    calculo.desglose = {"entradas": entradas, "contabilizados": contabilizados,
                       "preparacion": calculo.desglose.get("preparacion", {})}
    calculo.texto = _texto(cuenta, calculo.saldo_anterior, entradas, calculo.saldo_final)
    calculo.estado, calculo.confirmado_at = "CONFIRMADO", timezone.now()
    from patrimonio.services.proyeccion_cobros import resumen_mensual
    calculo.texto = resumen_mensual(cuenta, calculo.periodo, incluir_pendientes=False,
                                   calculo_pendiente=calculo).texto
    calculo.save()
    cuenta.sincronizar_activo()
    return calculo
