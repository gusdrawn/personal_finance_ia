"""Fuente única para las perspectivas de caja y consumo de Gastos Mensuales."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Q

from banca.models import EventoFinancieroExterno, ImportacionEstadoCuenta, MovimientoBancario
from configuracion.models import Producto, TipoCambio
from gastos.models import MovimientoManual


ZERO = Decimal("0")


def primer_dia(value: date) -> date:
    return value.replace(day=1)


def siguiente_mes(value: date) -> date:
    return date(value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1)


def convertir_a_clp(monto: Decimal, moneda: str, fecha: date) -> tuple[Decimal, Decimal | None, bool]:
    """Convierte conservando si el valor es una estimación de mercado."""
    if moneda == "CLP":
        return monto, None, False
    cambio = TipoCambio.objects.filter(fecha__lte=fecha).order_by("-fecha", "-id").first()
    if not cambio:
        cambio = TipoCambio.objects.order_by("-fecha", "-id").first()
    if not cambio:
        return ZERO, None, True
    tasa = cambio.dolar if moneda == "USD" else cambio.uf
    return (monto * tasa).quantize(Decimal("1")), tasa, True


def monto_facturado_clp(importacion: ImportacionEstadoCuenta) -> tuple[Decimal, bool]:
    if importacion.monto_pagado_clp is not None:
        return importacion.monto_pagado_clp, False
    if importacion.moneda == "CLP":
        return importacion.monto_facturado or ZERO, False
    if importacion.monto_facturado_clp_estimado is not None:
        return importacion.monto_facturado_clp_estimado, True
    referencia = importacion.fecha_vencimiento or importacion.fecha_estado or date.today()
    estimado, _, _ = convertir_a_clp(importacion.monto_facturado or ZERO, importacion.moneda, referencia)
    return estimado, True


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
    metadata=None,
):
    return {
        "key": str(key),
        "direccion": direccion,
        "categoria_id": categoria.pk if categoria else None,
        "categoria": categoria_nombre or (categoria.nombre if categoria else "Sin categoría"),
        "monto_clp": monto,
        "fecha": fecha,
        "descripcion": descripcion,
        "fuente": fuente,
        "producto_id": producto.pk if producto else None,
        "producto": producto.nombre if producto else "",
        "banco_id": banco.pk if banco else None,
        "banco": banco.nombre if banco else "",
        "esencialidad": esencialidad or (categoria.esencialidad_defecto if categoria else ""),
        "estado": estado,
        "estimado": estimado,
        "naturaleza": naturaleza,
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


def _lineas_movimiento(movimiento, *, periodo_caja=None):
    """Desglosa Neat cuando existe; el cargo padre deja de asignarse a categorías."""
    banco = movimiento.producto.banco
    eventos_neat = _neat_para_movimiento(movimiento) if movimiento.es_cargo else []
    movimiento_es_activo = bool(
        movimiento.prestamo_id
        or movimiento.detalles_cuentas_por_cobrar.exists()
    )
    if eventos_neat:
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
                    metadata={"movimiento_id": movimiento.pk, "evento_id": evento.pk},
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
                )
            )
        return lineas

    es_activo = movimiento_es_activo
    no_consumo = es_activo or movimiento.naturaleza != "OPERACIONAL"
    direccion = "EGRESO" if movimiento.es_cargo else "INGRESO"
    fuente = "CUENTA" if movimiento.producto.tipo == "CUENTA" else "TDC"
    return [
        _linea(
            key=f"movimiento:{movimiento.pk}",
            direccion=direccion,
            categoria=movimiento.categoria if not no_consumo else None,
            categoria_nombre="Conversión a activo" if es_activo else ("Transferencia / pago" if no_consumo else None),
            monto=abs(movimiento.monto_clp),
            fecha=periodo_caja or movimiento.fecha,
            descripcion=movimiento.descripcion,
            fuente=fuente,
            producto=movimiento.producto,
            banco=banco,
            esencialidad=movimiento.esencialidad,
            estado=movimiento.estado,
            naturaleza="ACTIVO_RECUPERABLE" if es_activo else movimiento.naturaleza,
        )
    ]


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
        )
        .filter(criterio_tdc | criterio_cuenta)
        .select_related("producto", "producto__banco")
        .prefetch_related(
            "movimientos__categoria",
            "movimientos__producto__banco",
            "movimientos__prestamo",
            "movimientos__detalles_cuentas_por_cobrar",
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
            if movimiento.pk in vistos or movimiento.estado == "DESCARTADO_CIERRE":
                continue
            vistos.add(movimiento.pk)
            detalles.extend(_lineas_movimiento(movimiento, periodo_caja=estado.fecha_vencimiento))
        # Una cuenta corriente ya trae cargos y abonos efectivos; no existe un
        # monto facturado contra el cual generar una línea de diferencia.
        if estado.producto.tipo == "CUENTA":
            lineas.extend(detalles)
            continue
        asignado = sum(
            (
                item["monto_clp"] if item["direccion"] == "EGRESO" else -item["monto_clp"]
                for item in detalles
            ),
            ZERO,
        )
        diferencia = oficial - asignado
        lineas.extend(detalles)
        if diferencia:
            lineas.append(
                _linea(
                    key=f"facturado-diferencia:{estado.pk}",
                    direccion="EGRESO",
                    categoria_nombre="Diferencia con facturado",
                    monto=diferencia,
                    fecha=estado.fecha_vencimiento,
                    descripcion=f"Cuadre del estado {estado.nombre_archivo}",
                    fuente="TDC",
                    producto=estado.producto,
                    banco=estado.producto.banco,
                    estado="DIFERENCIA",
                    estimado=estimado,
                    metadata={"importacion_id": estado.pk, "facturado_clp": oficial},
                )
            )

    manuales = (
        MovimientoManual.objects.filter(user=user, periodo__gte=desde, periodo__lte=hasta)
        .exclude(naturaleza="TRANSFERENCIA_INTERNA")
        .select_related("categoria", "departamento")
    )
    for manual in manuales:
        lineas.append(
            _linea(
                key=f"manual:{manual.pk}",
                direccion=manual.direccion,
                categoria=manual.categoria,
                monto=manual.monto_clp,
                fecha=manual.fecha,
                descripcion=manual.descripcion,
                fuente="MANUAL",
                esencialidad=manual.categoria.esencialidad_defecto,
                estimado=manual.monto_clp_estimado,
                naturaleza=manual.naturaleza,
                metadata={"departamento_id": manual.departamento_id},
            )
        )

    # Un reintegro recupera liquidez y reduce el activo pendiente. Es entrada
    # de caja en su fecha efectiva, pero no ingreso económico ni consumo.
    from patrimonio.models import AbonoCuentaPorCobrar

    abonos_cuentas = (
        AbonoCuentaPorCobrar.objects.filter(
            cuenta__user=user,
            fecha__gte=desde,
            fecha__lt=siguiente_mes(hasta),
        )
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
                fecha=abono.fecha,
                descripcion=abono.descripcion
                or (abono.detalle.descripcion if abono.detalle_id else abono.cuenta.nombre),
                fuente="MANUAL",
                estimado=estimado,
                naturaleza="ACTIVO_RECUPERABLE",
                metadata={
                    "cuenta_por_cobrar_id": abono.cuenta_id,
                    "detalle_id": abono.detalle_id,
                    "moneda_original": abono.cuenta.moneda,
                    "monto_original": abono.monto,
                    "tipo_cambio": tasa,
                },
            )
        )

    liquidaciones = (
        EventoFinancieroExterno.objects.filter(
            user=user,
            fuente="houm",
            estado="CONFIRMADO",
            periodo__gte=desde,
            periodo__lte=hasta,
            liquidacion_houm__vinculo_propiedad__isnull=False,
            liquidacion_houm__vinculo_propiedad__activo=True,
        )
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
                fecha=evento.periodo,
                descripcion=f"Arriendo {departamento.codigo}",
                fuente="HOUM",
                estado=evento.estado,
                metadata={
                    "departamento_id": departamento.pk,
                    "renta_bruta": liquidacion.renta_bruta,
                    "comision": evento.comision,
                    "descuentos": evento.descuentos,
                },
            )
        )
    return lineas, estados


def _lineas_consumo(user, desde, hasta):
    lineas = []
    movimientos = (
        MovimientoBancario.objects.filter(
            user=user,
            fecha__gte=desde,
            fecha__lt=siguiente_mes(hasta),
            por_revisar=False,
        )
        .exclude(estado="DESCARTADO_CIERRE")
        .select_related("categoria", "producto", "producto__banco", "prestamo")
        .prefetch_related(
            "detalles_cuentas_por_cobrar",
            "conciliaciones_externas__evento__categoria",
            "conciliaciones_externas__evento__pago_neat",
            "conciliaciones_externas__evento__detalles_cuentas_por_cobrar",
        )
    )
    for movimiento in movimientos:
        if movimiento.prestamo_id or movimiento.detalles_cuentas_por_cobrar.exists():
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
    ).select_related("categoria")
    for manual in manuales:
        lineas.append(
            _linea(
                key=f"manual:{manual.pk}",
                direccion=manual.direccion,
                categoria=manual.categoria,
                monto=manual.monto_clp,
                fecha=manual.fecha,
                descripcion=manual.descripcion,
                fuente="MANUAL",
                esencialidad=manual.categoria.esencialidad_defecto,
                estimado=manual.monto_clp_estimado,
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
        and (not estado or item["estado"] == estado)
    ]


def _agrupar(lineas):
    grupos = defaultdict(lambda: {"total_clp": ZERO, "cantidad": 0, "movimientos": []})
    for item in lineas:
        key = (item["direccion"], item["categoria_id"], item["categoria"])
        grupos[key]["total_clp"] += item["monto_clp"]
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
    productos = Producto.objects.filter(user=user, tipo="TDC", activo=True, reemplazado_por__isnull=True).select_related("banco")
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
                "saldo_final_clp": estado.saldo_final_clp or ZERO,
                "estimado": estimado,
                "pago_real_registrado": estado.monto_pagado_clp is not None,
                "nombre_archivo": estado.nombre_archivo,
            }
        )
    return resultado


def resumen_gastos(user, desde: date, hasta: date, perspectiva="consumo", filtros=None):
    desde, hasta = primer_dia(desde), primer_dia(hasta)
    filtros = filtros or {}
    if perspectiva == "consumo":
        lineas = _lineas_consumo(user, desde, hasta)
        estados = _estados_con_movimientos(user, desde, hasta)
    else:
        lineas, estados = _lineas_caja(user, desde, hasta)
    lineas = _filtrar(lineas, filtros)
    ingresos = sum((item["monto_clp"] for item in lineas if item["direccion"] == "INGRESO"), ZERO)
    egresos = sum((item["monto_clp"] for item in lineas if item["direccion"] == "EGRESO"), ZERO)
    return {
        "perspectiva": perspectiva,
        "desde": desde,
        "hasta": hasta,
        "totales": {"ingresos_clp": ingresos, "egresos_clp": egresos, "balance_clp": ingresos - egresos},
        "categorias": _agrupar(lineas),
        "estados_cuenta": _resumen_estados(estados, filtros),
        "cobertura": _cobertura(user, desde, hasta, estados) if perspectiva == "caja" else [],
        "cantidad": len(lineas),
    }
