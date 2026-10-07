"""Fuente única para las perspectivas de caja y consumo de Gastos Mensuales."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Q

from banca.models import (
    ConciliacionEventoExterno,
    EventoFinancieroExterno,
    ImportacionEstadoCuenta,
    MovimientoBancario,
)
from configuracion.models import Producto
from configuracion.services.tipos_cambio import tipo_cambio_para
from gastos.models import ConciliacionMovimientoManual, MovimientoManual


ZERO = Decimal("0")


def primer_dia(value: date) -> date:
    return value.replace(day=1)


def siguiente_mes(value: date) -> date:
    return date(value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1)


def convertir_a_clp(monto: Decimal, moneda: str, fecha: date) -> tuple[Decimal, Decimal | None, bool]:
    """Convierte conservando si el valor es una estimación de mercado."""
    if moneda == "CLP":
        return monto, None, False
    cambio = tipo_cambio_para(fecha)
    if not cambio:
        return ZERO, None, True
    tasa = cambio.dolar if moneda == "USD" else cambio.uf
    if not tasa:
        return ZERO, None, True
    return (monto * tasa).quantize(Decimal("1")), tasa, True


def monto_facturado_clp(importacion: ImportacionEstadoCuenta) -> tuple[Decimal | None, bool]:
    if importacion.monto_pagado_clp is not None:
        return importacion.monto_pagado_clp, False
    if importacion.moneda == "CLP":
        return importacion.monto_facturado or ZERO, False
    if importacion.monto_facturado_clp_estimado is not None and (
        importacion.monto_facturado_clp_estimado or not importacion.monto_facturado or importacion.tipo_cambio_estimado
    ):
        return importacion.monto_facturado_clp_estimado, True
    referencia = importacion.fecha_vencimiento or importacion.fecha_estado or date.today()
    estimado, tasa, _ = convertir_a_clp(importacion.monto_facturado or ZERO, importacion.moneda, referencia)
    return (None if importacion.monto_facturado and not tasa else estimado), True


def _linea(
    *,
    key,
    direccion,
    categoria=None,
    categoria_nombre=None,
    monto=ZERO,
    fecha=None,
    descripcion="",
    fuente="MANUAL",
    producto=None,
    banco=None,
    esencialidad="",
    estado="CONFIRMADO",
    estimado=False,
    naturaleza="OPERACIONAL",
    calculo="Incluido en el cálculo",
    monto_calculo_clp=None,
    metadata=None,
    conversion_desconocida=False,
):
    aporte_calculo = monto if monto_calculo_clp is None else monto_calculo_clp
    return {
        "key": str(key),
        "direccion": direccion,
        "categoria_id": categoria.pk if categoria else None,
        "categoria": categoria_nombre or (categoria.nombre if categoria else "Sin categoría"),
        "monto_clp": None if conversion_desconocida else monto,
        "fecha": fecha,
        "descripcion": descripcion,
        "fuente": fuente,
        "monto_calculo_clp": None if conversion_desconocida else aporte_calculo,
        "producto_id": producto.pk if producto else None,
        "producto": producto.nombre if producto else "",
        "banco_id": banco.pk if banco else None,
        "banco": banco.nombre if banco else "",
        "esencialidad": esencialidad or (categoria.esencialidad_defecto if categoria else ""),
        "estado": estado,
        "estimado": estimado,
        "naturaleza": naturaleza,
        "calculo": "Conversión CLP pendiente; no incluida en el subtotal conocido" if conversion_desconocida else calculo,
        "conversion_desconocida": conversion_desconocida,
        "metadata": metadata or {},
    }


def _neat_para_movimiento(movimiento):
    return [
        conciliacion.evento
        for conciliacion in movimiento.conciliaciones_externas.all()
        if conciliacion.rol == "CARGO"
        and conciliacion.evento.fuente == "neat"
        and conciliacion.evento.estado not in {"FALLIDO", "REVERSADO"}
    ]


def _entrada_identificada(movimiento, user):
    if movimiento.es_cargo:
        return False
    if movimiento.prestamo_id:
        return True
    return any(
        conciliacion.rol == "DEPOSITO"
        and conciliacion.evento.user_id == user.pk
        and conciliacion.evento.estado == "CONFIRMADO"
        for conciliacion in movimiento.conciliaciones_externas.all()
    )


def _calculo_movimiento(movimiento, *, periodo_caja=None):
    """Explica el papel visible de un movimiento en cada perspectiva."""
    if periodo_caja and movimiento.producto.tipo == "TDC":
        if movimiento.naturaleza != "OPERACIONAL":
            return "TDC: pago/transferencia usado para cuadrar el facturado"
        if not movimiento.es_cargo:
            return "TDC: devolución/abono resta del neto facturado"
        return "TDC: cargo incluido en el cuadre"
    if movimiento.producto.tipo == "TDC" and not movimiento.es_cargo:
        return "Consumo: abono TDC reduce el egreso; no es ingreso"
    if periodo_caja:
        return "Caja: movimiento bancario"
    return "Consumo: movimiento operacional"


def _lineas_movimiento(movimiento, *, periodo_caja=None, desglosar_neat=True):
    """Desglosa Neat cuando existe; el cargo padre deja de asignarse a categorías."""
    banco = movimiento.producto.banco
    comprobante = next((item for item in movimiento.comprobantes_pago.all() if item.user_id == movimiento.user_id and item.tipo_documento == "PAGO_ASEO" and item.estado == "PROCESADO"), None)
    if comprobante and movimiento.naturaleza == "OPERACIONAL" and not movimiento.prestamo_id and not movimiento.detalles_cuentas_por_cobrar.exists():
        asignaciones = [item for item in comprobante.asignaciones_pendientes.all() if item.ocurrencia.regla.user_id == movimiento.user_id and item.ocurrencia.estado == "COMPLETADA"]
        lineas, asignado = [], ZERO
        for asignacion in asignaciones:
            asignado += asignacion.monto_asignado
            regla = asignacion.ocurrencia.regla
            lineas.append(_linea(
                key=f"aseo-banco:{asignacion.pk}", direccion="EGRESO", categoria=movimiento.categoria,
                monto=asignacion.monto_asignado, fecha=periodo_caja or movimiento.fecha,
                descripcion=regla.nombre, fuente="CUENTA" if movimiento.producto.tipo == "CUENTA" else "TDC",
                producto=movimiento.producto, banco=banco, estado=movimiento.estado, esencialidad="FIJO",
                calculo="Pago bancario respaldado por certificado municipal",
                metadata={"movimiento_id": movimiento.pk, "importacion_id": comprobante.pk, "departamento_id": regla.departamento_id},
            ))
        resto = movimiento.monto_clp - asignado
        if resto:
            lineas.append(_linea(
                key=f"aseo-banco-resto:{movimiento.pk}", direccion="EGRESO", categoria=movimiento.categoria,
                monto=resto, fecha=periodo_caja or movimiento.fecha, descripcion="Aseo pendiente de distribuir",
                fuente="CUENTA" if movimiento.producto.tipo == "CUENTA" else "TDC", producto=movimiento.producto,
                banco=banco, estado=movimiento.estado, metadata={"movimiento_id": movimiento.pk},
            ))
        return lineas
    eventos_neat = _neat_para_movimiento(movimiento) if movimiento.es_cargo else []
    movimiento_es_activo = bool(
        movimiento.prestamo_id
        or movimiento.detalles_cuentas_por_cobrar.exists()
    )
    if eventos_neat and desglosar_neat:
        lineas = []
        asignado = ZERO
        for evento in eventos_neat:
            pago = getattr(evento, "pago_neat", None)
            monto = evento.monto_neto or evento.monto_bruto
            asignado += monto
            evento_es_activo = movimiento_es_activo or evento.detalles_cuentas_por_cobrar.exists()
            lineas.append(
                _linea(
                    key=f"neat:{evento.pk}",
                    direccion="EGRESO",
                    categoria=None if evento_es_activo else (evento.categoria or movimiento.categoria),
                    categoria_nombre="Cuenta por cobrar" if evento_es_activo else None,
                    monto=monto,
                    fecha=periodo_caja or evento.fecha_cargo or movimiento.fecha,
                    descripcion=(pago.destinatario or pago.cuenta) if pago else "NeatPagos",
                    fuente="NEAT",
                    producto=movimiento.producto,
                    banco=banco,
                    estado=evento.estado,
                    naturaleza="ACTIVO_RECUPERABLE" if evento_es_activo else movimiento.naturaleza,
                    calculo=_calculo_movimiento(movimiento, periodo_caja=periodo_caja),
                    metadata={
                        "movimiento_id": movimiento.pk, "evento_id": evento.pk,
                        "estado_movimiento": movimiento.estado,
                    },
                )
            )
        diferencia = movimiento.monto_clp - asignado
        if diferencia:
            lineas.append(
                _linea(
                    key=f"neat-diferencia:{movimiento.pk}",
                    direccion="EGRESO",
                    categoria_nombre="Diferencia Neat",
                    monto=diferencia,
                    fecha=periodo_caja or movimiento.fecha,
                    descripcion="Diferencia entre el cargo bancario y sus pagos Neat",
                    fuente="NEAT",
                    producto=movimiento.producto,
                    banco=banco,
                    estado="DIFERENCIA",
                    calculo="TDC: diferencia interna del cargo contra sus pagos conciliados",
                    metadata={"movimiento_id": movimiento.pk, "estado_movimiento": movimiento.estado},
                )
            )
        return lineas

    es_activo = movimiento_es_activo
    no_consumo = es_activo or movimiento.naturaleza != "OPERACIONAL"
    es_abono_tdc = (
        movimiento.producto.tipo == "TDC"
        and not movimiento.es_cargo
        and movimiento.naturaleza == "OPERACIONAL"
    )
    es_pago_tdc = (
        movimiento.producto.tipo == "TDC"
        and not movimiento.es_cargo
        and movimiento.naturaleza != "OPERACIONAL"
    )
    direccion = "EGRESO" if movimiento.es_cargo or es_abono_tdc or es_pago_tdc else "INGRESO"
    monto = -abs(movimiento.monto_clp) if es_abono_tdc else abs(movimiento.monto_clp)
    calculo = _calculo_movimiento(movimiento, periodo_caja=periodo_caja)
    if es_pago_tdc:
        calculo = "TDC: pago/transferencia no es ingreso ni gasto; no suma al total"
    fuente = "CUENTA" if movimiento.producto.tipo == "CUENTA" else "TDC"
    return [
        _linea(
            key=f"movimiento:{movimiento.pk}",
            direccion=direccion,
            categoria=movimiento.categoria if not no_consumo else None,
            categoria_nombre=(
                "Recuperación bicicleta" if movimiento.prestamo_id and not movimiento.es_cargo
                else "Conversión a activo" if es_activo
                else "Transferencia / pago" if no_consumo else None
            ),
            monto=monto,
            fecha=periodo_caja or movimiento.fecha,
            descripcion=movimiento.descripcion,
            fuente=fuente,
            producto=movimiento.producto,
            banco=banco,
            esencialidad=movimiento.esencialidad,
            estado=movimiento.estado,
            naturaleza="ACTIVO_RECUPERABLE" if es_activo else movimiento.naturaleza,
            calculo=calculo,
            monto_calculo_clp=ZERO if es_pago_tdc else monto,
            conversion_desconocida=movimiento.conversion_clp_desconocida and not es_pago_tdc,
            metadata={"movimiento_id": movimiento.pk, "estado_movimiento": movimiento.estado,
                      "monto_original": movimiento.monto, "moneda_original": movimiento.moneda,
                      "cuota_total": movimiento.cuota_total},
        )
    ]


def _metadata_manual(manual):
    return {
        "departamento_id": manual.departamento_id,
        "periodo_contable": manual.periodo,
        "resumen_mensual": manual.es_resumen_mensual,
        "modo_resumen": manual.modo_resumen,
        "moneda_original": manual.moneda,
        "monto_original": manual.monto,
    }


def _ajustar_resumenes_mensuales(lineas):
    """Un total declarado completa el detalle de su mes; nunca lo reduce."""
    detalle = [item for item in lineas if not item["metadata"].get("resumen_mensual")]
    for resumen in lineas:
        metadata = resumen["metadata"]
        if not metadata.get("resumen_mensual") or metadata["modo_resumen"] != "TOTAL":
            continue
        if resumen['conversion_desconocida']:
            continue
        cubierto = sum(((item["monto_calculo_clp"] or ZERO) for item in detalle
            if item["categoria_id"] == resumen["categoria_id"]
            and item["direccion"] == resumen["direccion"]
            and item["naturaleza"] == resumen["naturaleza"]
            and (item["metadata"].get("periodo_contable") or primer_dia(item["fecha"])) == metadata["periodo_contable"]), ZERO)
        cubierto = max(cubierto, ZERO)
        resumen["monto_calculo_clp"] = max(resumen["monto_clp"] - cubierto, ZERO)
        resumen["metadata"]["cubierto_clp"] = cubierto
        resumen["calculo"] = "Total mensual: aporta sólo el importe que falta tras los movimientos de la categoría"
    return lineas


def _estados_vigentes(user, desde, hasta):
    # Los estados de tarjeta se imputan al vencimiento; las cartolas de cuenta
    # corriente no tienen fecha de vencimiento ni monto facturado y se explican
    # por su fecha de estado/movimientos. Mantener ambas ramas aquí evita que los
    # abonos de sueldo de una cuenta desaparezcan de la perspectiva de caja.
    criterio_tdc = Q(
        producto__tipo="TDC",
        fecha_vencimiento__gte=desde,
        fecha_vencimiento__lt=siguiente_mes(hasta),
        monto_facturado__isnull=False,
    )
    criterio_cuenta = Q(
        producto__tipo="CUENTA",
        fecha_estado__gte=desde,
        fecha_estado__lt=siguiente_mes(hasta),
    )
    return list(
        ImportacionEstadoCuenta.objects.filter(
            user=user,
            vigente=True,
            estado="PROCESADO",
            producto__isnull=False,
            producto__user=user, producto__contabilizar=True,
        )
        .filter(criterio_tdc | criterio_cuenta)
        .select_related("producto", "producto__banco")
        .prefetch_related(
            "movimientos__categoria",
            "movimientos__producto__banco",
            "movimientos__prestamo",
            "movimientos__detalles_cuentas_por_cobrar",
            "movimientos__abonos_cuentas_por_cobrar",
            "movimientos__comprobantes_pago__asignaciones_pendientes__ocurrencia__regla",
            "movimientos__conciliaciones_externas__evento__categoria",
            "movimientos__conciliaciones_externas__evento__pago_neat",
            "movimientos__conciliaciones_externas__evento__detalles_cuentas_por_cobrar",
        )
        .order_by("fecha_vencimiento", "producto_id", "moneda", "-created_at")
    )


def _estados_con_movimientos(user, desde, hasta):
    """Estados que explican consumos ocurridos en el período seleccionado."""
    return list(
        ImportacionEstadoCuenta.objects.filter(
            user=user,
            vigente=True,
            estado="PROCESADO",
            producto__isnull=False,
            producto__user=user, producto__contabilizar=True,
            movimientos__user=user,
            movimientos__fecha__gte=desde,
            movimientos__fecha__lt=siguiente_mes(hasta),
        )
        .select_related("producto", "producto__banco")
        .distinct()
        .order_by("fecha_estado", "producto_id", "moneda", "-created_at")
    )


def _lineas_caja(user, desde, hasta):
    lineas = []
    estados = _estados_vigentes(user, desde, hasta)
    for estado in estados:
        oficial, estimado = monto_facturado_clp(estado)
        detalles = []
        vistos = set()
        for movimiento in estado.movimientos.all():
            if movimiento.pk in vistos or movimiento.estado not in {"FACTURADO", "PAGADO"}:
                continue
            if movimiento.user_id != user.pk:
                continue
            if estado.producto.tipo == "CUENTA" and movimiento.por_revisar:
                continue
            if (estado.producto.tipo == "CUENTA" and movimiento.naturaleza != "OPERACIONAL"
                    and not _entrada_identificada(movimiento, user)):
                continue
            if estado.producto.tipo == "CUENTA" and not (desde <= movimiento.fecha < siguiente_mes(hasta)):
                continue
            vistos.add(movimiento.pk)
            if estado.producto.tipo == "CUENTA" and movimiento.abonos_cuentas_por_cobrar.exists():
                continue
            detalles.extend(_lineas_movimiento(
                movimiento,
                periodo_caja=estado.fecha_vencimiento if estado.producto.tipo == "TDC" else None,
                desglosar_neat=False,
            ))
        # Una cuenta corriente ya trae cargos y abonos efectivos; no existe un
        # monto facturado contra el cual generar una línea de diferencia.
        if estado.producto.tipo == "CUENTA":
            lineas.extend(detalles)
            continue
        asignado = sum(
            (
                (item["monto_calculo_clp"] or ZERO) if item["direccion"] == "EGRESO" else -(item["monto_calculo_clp"] or ZERO)
                for item in detalles
            ),
            ZERO,
        )
        diferencia = oficial - asignado if oficial is not None else None
        lineas.extend(detalles)
        if diferencia or oficial is None:
            lineas.append(
                _linea(
                    key=f"facturado-diferencia:{estado.pk}",
                    direccion="EGRESO",
                    categoria_nombre="Diferencia con facturado",
                    monto=diferencia or ZERO,
                    fecha=estado.fecha_vencimiento,
                    descripcion=f"Cuadre del estado {estado.nombre_archivo}",
                    fuente="TDC",
                    producto=estado.producto,
                    banco=estado.producto.banco,
                    estado="DIFERENCIA",
                    estimado=estimado,
                    conversion_desconocida=oficial is None,
                    calculo="TDC: ajuste automático para llegar al monto facturado",
                    metadata={"importacion_id": estado.pk, "facturado_clp": oficial},
                )
            )

    # Las transacciones facturadas de cuenta sin cartola siguen siendo caja real.
    # Excluir las ya enlazadas evita duplicarlas con los estados importados.
    ids_en_estados = {
        movimiento.pk
        for estado in estados if estado.producto.tipo == "CUENTA"
        for movimiento in estado.movimientos.all()
    }
    cuentas_sin_estado = (
        MovimientoBancario.objects.filter(
            user=user, producto__user=user, producto__tipo="CUENTA",
            producto__contabilizar=True,
            fecha__gte=desde, fecha__lt=siguiente_mes(hasta),
            estado__in=("FACTURADO", "PAGADO"), por_revisar=False,
        )
        .exclude(pk__in=ids_en_estados)
        .select_related("categoria", "producto", "producto__banco", "prestamo")
        .prefetch_related(
            "abonos_cuentas_por_cobrar", "detalles_cuentas_por_cobrar",
            "comprobantes_pago__asignaciones_pendientes__ocurrencia__regla",
            "conciliaciones_externas__evento",
        )
    )
    for movimiento in cuentas_sin_estado:
        if movimiento.naturaleza != "OPERACIONAL" and not _entrada_identificada(movimiento, user):
            continue
        if not movimiento.abonos_cuentas_por_cobrar.exists():
            lineas.extend(_lineas_movimiento(movimiento, desglosar_neat=False))

    manuales = (
        MovimientoManual.objects.filter(user=user, periodo__gte=desde, periodo__lte=hasta)
        .exclude(naturaleza="TRANSFERENCIA_INTERNA")
        .exclude(
            pk__in=ConciliacionMovimientoManual.objects.filter(
                movimiento_manual__user=user, movimiento_bancario__user=user,
                movimiento_bancario__producto__user=user, movimiento_bancario__producto__contabilizar=True,
                estado="CONFIRMADA", confirmada_manual=True,
            ).values("movimiento_manual_id")
        )
        .select_related("categoria", "departamento")
    )
    for manual in manuales:
        lineas.append(
            _linea(
                key=f"manual:{manual.pk}",
                direccion=manual.direccion,
                categoria=manual.categoria,
                monto=manual.monto_clp,
                conversion_desconocida=bool(manual.moneda != 'CLP' and manual.monto and not manual.tipo_cambio and not manual.monto_clp),
                fecha=manual.fecha,
                descripcion=manual.descripcion,
                fuente="MANUAL",
                esencialidad=manual.categoria.esencialidad_defecto,
                estimado=manual.monto_clp_estimado,
                calculo="Caja: movimiento manual",
                naturaleza=manual.naturaleza,
                metadata=_metadata_manual(manual),
            )
        )

    # Un certificado municipal importado es una fuente de gasto por sí mismo
    # cuando no existe movimiento bancario asociado. Se muestran sus
    # asignaciones por propiedad sin convertir el total agrupado del certificado
    # en un gasto repetido para cada rol.
    lineas.extend(_lineas_aseo_importado(user, desde, hasta))

    # Un reintegro recupera liquidez y reduce el activo pendiente. Es entrada
    # de caja en su fecha efectiva, pero no ingreso económico ni consumo.
    from patrimonio.models import AbonoCuentaPorCobrar

    abonos_cuentas = (
        AbonoCuentaPorCobrar.objects.filter(
            cuenta__user=user,
            fecha__gte=desde,
            fecha__lt=siguiente_mes(hasta),
        )
        .filter(Q(movimiento__isnull=True) | Q(movimiento__producto__user=user, movimiento__producto__contabilizar=True))
        .select_related("cuenta", "detalle")
        .order_by("fecha", "id")
    )
    for abono in abonos_cuentas:
        monto_clp, tasa, estimado = convertir_a_clp(
            abono.monto, abono.cuenta.moneda, abono.fecha
        )
        lineas.append(
            _linea(
                key=f"abono-cuenta-cobrar:{abono.pk}",
                direccion="INGRESO",
                categoria_nombre="Recuperación cuenta por cobrar",
                monto=monto_clp,
                conversion_desconocida=bool(abono.cuenta.moneda != 'CLP' and abono.monto and not tasa),
                fecha=abono.fecha,
                descripcion=abono.descripcion
                or (abono.detalle.descripcion if abono.detalle_id else abono.cuenta.nombre),
                fuente="MANUAL",
                estimado=estimado,
                naturaleza="ACTIVO_RECUPERABLE",
                calculo="Caja: recuperación de cuenta por cobrar; no es ingreso económico",
                metadata={
                    "cuenta_por_cobrar_id": abono.cuenta_id,
                    "detalle_id": abono.detalle_id,
                    "moneda_original": abono.cuenta.moneda,
                    "monto_original": abono.monto,
                    "tipo_cambio": tasa,
                },
            )
        )

    depositos_conciliados = ConciliacionEventoExterno.objects.filter(
        evento__user=user, rol="DEPOSITO", movimiento__isnull=False
    ).values("evento_id")
    liquidaciones = (
        EventoFinancieroExterno.objects.filter(
            user=user,
            fuente="houm",
            estado="CONFIRMADO",
            fecha_confirmacion__gte=desde,
            fecha_confirmacion__lt=siguiente_mes(hasta),
            liquidacion_houm__vinculo_propiedad__isnull=False,
            liquidacion_houm__vinculo_propiedad__activo=True,
        )
        .exclude(pk__in=depositos_conciliados)
        .select_related(
            "categoria",
            "liquidacion_houm",
            "liquidacion_houm__vinculo_propiedad__departamento",
        )
        .prefetch_related("liquidacion_houm__detalles")
    )
    for evento in liquidaciones:
        liquidacion = evento.liquidacion_houm
        departamento = liquidacion.vinculo_propiedad.departamento
        lineas.append(
            _linea(
                key=f"houm:{evento.pk}",
                direccion="INGRESO",
                categoria=evento.categoria,
                categoria_nombre=evento.categoria.nombre if evento.categoria else "Arriendos Houm",
                monto=liquidacion.deposito_neto,
                fecha=evento.fecha_confirmacion,
                descripcion=f"Arriendo {departamento.codigo}",
                fuente="HOUM",
                estado=evento.estado,
                calculo="Caja: ingreso Houm confirmado",
                metadata={
                    "departamento_id": departamento.pk,
                    "renta_bruta": liquidacion.renta_bruta,
                    "comision": evento.comision,
                    "descuentos": evento.descuentos,
                },
            )
        )
    return _ajustar_resumenes_mensuales(lineas), estados


def _lineas_consumo(user, desde, hasta):
    lineas = []
    movimientos = (
        MovimientoBancario.objects.filter(
            user=user,
            producto__user=user, producto__contabilizar=True,
            fecha__gte=desde,
            fecha__lt=siguiente_mes(hasta),
            por_revisar=False,
        )
        .exclude(estado="DESCARTADO_CIERRE")
        .select_related("categoria", "producto", "producto__banco", "prestamo")
        .prefetch_related(
            "detalles_cuentas_por_cobrar",
            "abonos_cuentas_por_cobrar",
            "comprobantes_pago__asignaciones_pendientes__ocurrencia__regla",
            "conciliaciones_externas__evento__categoria",
            "conciliaciones_externas__evento__pago_neat",
            "conciliaciones_externas__evento__detalles_cuentas_por_cobrar",
        )
    )
    for movimiento in movimientos:
        if movimiento.prestamo_id or movimiento.detalles_cuentas_por_cobrar.exists():
            continue
        if movimiento.abonos_cuentas_por_cobrar.exists():
            continue
        if movimiento.naturaleza != "OPERACIONAL":
            continue
        lineas.extend(
            linea
            for linea in _lineas_movimiento(movimiento)
            if linea["naturaleza"] == "OPERACIONAL"
        )

    manuales = MovimientoManual.objects.filter(
        user=user,
        periodo__gte=desde,
        periodo__lte=hasta,
        naturaleza="OPERACIONAL",
    ).exclude(
        pk__in=ConciliacionMovimientoManual.objects.filter(
            movimiento_manual__user=user, movimiento_bancario__user=user,
            movimiento_bancario__producto__user=user, movimiento_bancario__producto__contabilizar=True,
            estado="CONFIRMADA", confirmada_manual=True,
        ).values("movimiento_manual_id")
    ).select_related("categoria")
    for manual in manuales:
        lineas.append(
            _linea(
                key=f"manual:{manual.pk}",
                direccion=manual.direccion,
                categoria=manual.categoria,
                monto=manual.monto_clp,
                conversion_desconocida=bool(manual.moneda != 'CLP' and manual.monto and not manual.tipo_cambio and not manual.monto_clp),
                fecha=manual.fecha,
                descripcion=manual.descripcion,
                fuente="MANUAL",
                esencialidad=manual.categoria.esencialidad_defecto,
                estimado=manual.monto_clp_estimado,
                metadata=_metadata_manual(manual),
            )
        )
    lineas.extend(_lineas_aseo_importado(user, desde, hasta))
    return _ajustar_resumenes_mensuales(lineas)


def _lineas_aseo_importado(user, desde, hasta):
    from pendientes.models import AsignacionPagoPendiente

    asignaciones = (
        AsignacionPagoPendiente.objects.filter(
            ocurrencia__regla__user=user,
            ocurrencia__estado="COMPLETADA",
            importacion__user=user,
            importacion__tipo_documento="PAGO_ASEO",
            importacion__movimiento_pago__isnull=True,
            monto_asignado__gt=ZERO,
        )
        .filter(
            Q(ocurrencia__fecha_real__gte=desde, ocurrencia__fecha_real__lt=siguiente_mes(hasta))
            | Q(
                ocurrencia__fecha_real__isnull=True,
                importacion__fecha_pago_real__gte=desde,
                importacion__fecha_pago_real__lt=siguiente_mes(hasta),
            )
        )
        .select_related("ocurrencia__regla__departamento", "importacion")
    )
    lineas = []
    for asignacion in asignaciones:
        ocurrencia = asignacion.ocurrencia
        regla = ocurrencia.regla
        fecha = ocurrencia.fecha_real or asignacion.importacion.fecha_pago_real
        if not fecha:
            continue
        lineas.append(
            _linea(
                key=f"aseo-importado:{asignacion.pk}",
                direccion="EGRESO",
                categoria_nombre="Aseo",
                monto=asignacion.monto_asignado,
                fecha=fecha,
                descripcion=regla.nombre,
                fuente="ASEO_IMPORTADO",
                esencialidad="FIJO",
                calculo="Aseo municipal: certificado importado",
                metadata={
                    "departamento_id": regla.departamento_id,
                    "importacion_id": asignacion.importacion_id,
                },
            )
        )
    return lineas


def _filtrar(lineas, filtros):
    categoria = str(filtros.get("categoria") or "")
    banco = str(filtros.get("banco") or "")
    producto = str(filtros.get("producto") or "")
    direccion = filtros.get("direccion") or ""
    fuente = (filtros.get("fuente") or "").upper()
    esencialidad = (filtros.get("esencialidad") or "").upper()
    estado = (filtros.get("estado") or "").upper()
    return [
        item
        for item in lineas
        if (not categoria or str(item["categoria_id"]) == categoria)
        and (not banco or str(item["banco_id"]) == banco)
        and (not producto or str(item["producto_id"]) == producto)
        and (not direccion or item["direccion"] == direccion)
        and (not fuente or item["fuente"] == fuente)
        and (not esencialidad or item["esencialidad"] == esencialidad)
        and (not estado or item["metadata"].get("estado_movimiento", item["estado"]) == estado)
    ]


def _agrupar(lineas):
    grupos = defaultdict(lambda: {"total_clp": ZERO, "cantidad": 0, "movimientos": []})
    for item in lineas:
        key = (item["direccion"], item["categoria_id"], item["categoria"])
        grupos[key]["total_clp"] += item["monto_calculo_clp"] or ZERO
        grupos[key]["cantidad"] += 1
        grupos[key]["movimientos"].append(item)
    salida = []
    for (direccion, categoria_id, nombre), valores in grupos.items():
        salida.append(
            {
                "direccion": direccion,
                "categoria_id": categoria_id,
                "categoria": nombre,
                **valores,
            }
        )
    return sorted(salida, key=lambda row: (row["direccion"] != "INGRESO", -row["total_clp"]))


def _cobertura(user, desde, hasta, estados):
    productos = Producto.objects.filter(user=user, tipo="TDC", activo=True, contabilizar=True, reemplazado_por__isnull=True).select_related("banco")
    presentes = {
        (estado.producto_id, primer_dia(estado.fecha_vencimiento))
        for estado in estados
        if estado.producto.tipo == "TDC" and estado.fecha_vencimiento
    }
    meses = []
    actual = desde
    while actual <= hasta:
        meses.append(actual)
        actual = siguiente_mes(actual)
    return [
        {
            "producto_id": producto.pk,
            "producto": producto.nombre,
            "banco": producto.banco.nombre,
            "periodo": periodo,
            "estado": "IMPORTADO" if (producto.pk, periodo) in presentes else "PENDIENTE",
            "monto_clp": ZERO,
        }
        for periodo in meses
        for producto in productos
        if (producto.pk, periodo) not in presentes
    ]


def _resumen_estados(estados, filtros):
    """Ficha visible de cada estado; no altera los totales ni repite movimientos."""
    banco = str(filtros.get("banco") or "")
    producto = str(filtros.get("producto") or "")
    fuente = (filtros.get("fuente") or "").upper()
    resultado = []
    for estado in estados:
        fuente_estado = "CUENTA" if estado.producto.tipo == "CUENTA" else "TDC"
        if fuente and fuente != fuente_estado:
            continue
        if banco and str(estado.producto.banco_id) != banco:
            continue
        if producto and str(estado.producto_id) != producto:
            continue
        oficial, estimado = monto_facturado_clp(estado)
        es_cuenta = estado.producto.tipo == "CUENTA"
        resultado.append(
            {
                "id": estado.pk,
                "banco": estado.producto.banco.nombre,
                "producto_id": estado.producto_id,
                "producto": estado.producto.nombre,
                "ultimos4": estado.tarjeta_ultimos4 or estado.producto.ultimos4,
                "fecha_estado": estado.fecha_estado,
                "fecha_vencimiento": estado.fecha_vencimiento,
                "es_cuenta": es_cuenta,
                "moneda": estado.moneda,
                "monto_facturado": estado.monto_facturado or ZERO,
                "monto_clp": oficial,
                "conversion_desconocida": oficial is None,
                "saldo_final_clp": estado.saldo_final_clp or ZERO,
                "estimado": estimado,
                "pago_real_registrado": estado.monto_pagado_clp is not None,
                "nombre_archivo": estado.nombre_archivo,
            }
        )
    return resultado


def resumen_gastos(user, desde: date, hasta: date, perspectiva="caja", filtros=None):
    desde, hasta = primer_dia(desde), primer_dia(hasta)
    filtros = filtros or {}
    if perspectiva == "consumo":
        lineas = _lineas_consumo(user, desde, hasta)
        estados = _estados_con_movimientos(user, desde, hasta)
    else:
        lineas, estados = _lineas_caja(user, desde, hasta)
    lineas_calculo = lineas
    lineas_visibles = _filtrar(lineas, filtros)
    ingresos = sum(((item["monto_calculo_clp"] or ZERO) for item in lineas_calculo if item["direccion"] == "INGRESO"), ZERO)
    egresos = sum(((item["monto_calculo_clp"] or ZERO) for item in lineas_calculo if item["direccion"] == "EGRESO"), ZERO)
    sin_conversion = sum(item['conversion_desconocida'] for item in lineas_calculo)
    return {
        "perspectiva": perspectiva,
        "desde": desde,
        "hasta": hasta,
        "totales": {"ingresos_clp": ingresos, "egresos_clp": egresos, "balance_clp": ingresos - egresos},
        "totales_completos": sin_conversion == 0,
        "sin_conversion": sin_conversion,
        "categorias": _agrupar(lineas_visibles),
        "estados_cuenta": _resumen_estados(estados, filtros),
        "cobertura": _cobertura(user, desde, hasta, estados) if perspectiva == "caja" else [],
        "cantidad": len(lineas_visibles),
    }
