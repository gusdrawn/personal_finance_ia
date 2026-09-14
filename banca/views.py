"""Vistas de banca: dashboard de presupuesto/deudas y bandeja de movimientos."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
import json

from django.contrib import messages
from django.db.models import Q, Sum
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from banca.forms import (
    CategoriaTematicaForm,
    ConfiguracionBancaForm,
    ContraparteConocidaForm,
    CredencialBancoForm,
    ProgramacionFuenteForm,
)
from banca.models import (
    CategoriaTematica,
    ApplePayApiKey,
    ConfiguracionBanca,
    ContraparteConocida,
    CredencialBanco,
    ImportacionEstadoCuenta,
    ImportacionFuenteExterna,
    MapeoComercio,
    MovimientoBancario,
    SaldoSnapshot,
    SyncLog,
    EventoFinancieroExterno,
    LiquidacionHoum,
    VinculoPropiedadExterna,
)
from banca.services import analisis, presupuesto, reconciliacion
from banca.services.apple_pay import (
    ApplePayEventConflict,
    ApplePayPayloadError,
    autenticar_api_key,
    registrar_pago,
)
from banca.services.estados_cuenta import importar_estado_cuenta, reanalizar_importacion
from banca.services.clasificacion_ia import (
    clasificar_pendientes,
    guardar_mapeo_comercio,
    recalcular_clasificaciones,
)
from configuracion.models import Banco, Producto
from patrimonio.models import CuentaPorCobrar


@login_required
def banca_dashboard(request):
    user = request.user
    hoy = timezone.localdate()
    pres = presupuesto.resumen_presupuesto(user)
    fo = analisis.fijo_vs_ocio(user, hoy.year, hoy.month)

    context = {
        "presupuesto": pres,
        "fijo_ocio": fo,
        "categorias_mes": analisis.por_categoria(user, hoy.year, hoy.month)[:8],
        "pagar_tdc": analisis.monto_a_pagar_tdc(user),
        "cupos_tdc": analisis.cupos_tdc(user),
        "intereses": analisis.total_intereses(user, hoy.year, hoy.month),
        "suscripciones": analisis.detectar_suscripciones(user)[:8],
        "por_revisar": MovimientoBancario.objects.filter(
            user=user, por_revisar=True
        ).count(),
        "ultimos": MovimientoBancario.objects.filter(user=user).select_related(
            "categoria", "producto"
        )[:12],
        "tiene_credencial": CredencialBanco.objects.filter(
            user=user, activo=True
        ).exists(),
    }
    return render(request, "banca/dashboard.html", context)


def _apple_pay_error(message: str, *, code: str, status: int, field: str | None = None):
    error = {"code": code, "message": message}
    if field:
        error["field"] = field
    return JsonResponse({"ok": False, "error": error}, status=status)


@csrf_exempt
@require_POST
def banca_apple_pay_ingest(request):
    """Recibe un pago desde Apple Shortcuts usando `Authorization: Bearer ...`."""
    authorization = request.headers.get("Authorization", "")
    secreto = ""
    if authorization.lower().startswith("bearer "):
        secreto = authorization[7:].strip()
    secreto = secreto or request.headers.get("X-API-Key", "")
    api_key = autenticar_api_key(secreto)
    if not api_key:
        response = _apple_pay_error(
            "API key ausente, inválida o revocada.",
            code="invalid_api_key",
            status=401,
        )
        response["WWW-Authenticate"] = "Bearer"
        return response

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _apple_pay_error(
            "El cuerpo debe ser JSON válido.", code="invalid_json", status=400
        )

    try:
        resultado = registrar_pago(api_key.user, payload, api_key=api_key)
    except ApplePayPayloadError as exc:
        return _apple_pay_error(
            str(exc), code="invalid_payload", status=422, field=exc.field
        )
    except ApplePayEventConflict as exc:
        return _apple_pay_error(str(exc), code="event_id_conflict", status=409)

    return JsonResponse(
        {"ok": True, **resultado},
        status=200 if resultado["duplicated"] else 201,
    )


@login_required
@require_http_methods(["GET", "POST"])
def banca_apple_pay_keys(request):
    """Lista llaves propias o crea una nueva y devuelve el secreto una sola vez."""
    if request.method == "GET":
        keys = ApplePayApiKey.objects.filter(user=request.user)
        return JsonResponse(
            {
                "keys": [
                    {
                        "id": key.id,
                        "name": key.nombre,
                        "prefix": key.prefijo,
                        "active": bool(key.activo and not key.revocado_at),
                        "last_used_at": key.ultimo_uso_at.isoformat()
                        if key.ultimo_uso_at
                        else None,
                        "created_at": key.created_at.isoformat(),
                    }
                    for key in keys
                ]
            }
        )

    nombre = request.POST.get("name", "Atajo iPhone")
    if request.content_type.startswith("application/json"):
        try:
            body = json.loads(request.body.decode("utf-8"))
            nombre = body.get("name", nombre)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _apple_pay_error("El cuerpo debe ser JSON válido.", code="invalid_json", status=400)
    key, secreto = ApplePayApiKey.crear(request.user, nombre=nombre)
    return JsonResponse(
        {
            "id": key.id,
            "name": key.nombre,
            "prefix": key.prefijo,
            "api_key": secreto,
            "warning": "Guárdala ahora en el iPhone; no volverá a mostrarse.",
        },
        status=201,
    )


@login_required
@require_POST
def banca_apple_pay_key_revoke(request, pk):
    key = get_object_or_404(ApplePayApiKey, pk=pk, user=request.user)
    key.activo = False
    key.revocado_at = timezone.now()
    key.save(update_fields=["activo", "revocado_at", "updated_at"])
    return JsonResponse({"ok": True, "revoked": True, "id": key.id})


@login_required
def banca_movimientos(request):
    user = request.user
    base_qs = MovimientoBancario.objects.filter(user=user)

    texto = request.GET.get("q", "").strip()
    fecha_desde = parse_date(request.GET.get("fecha_desde", ""))
    fecha_hasta = parse_date(request.GET.get("fecha_hasta", ""))
    estado = request.GET.get("estado")
    naturaleza = request.GET.get("naturaleza")
    origen = request.GET.get("origen")
    categoria = request.GET.get("categoria")
    banco = request.GET.get("banco")
    producto = request.GET.get("producto")
    revisar = request.GET.get("revisar")

    if texto:
        base_qs = base_qs.filter(
            Q(descripcion__icontains=texto)
            | Q(producto__nombre__icontains=texto)
            | Q(producto__banco__nombre__icontains=texto)
            | Q(categoria__nombre__icontains=texto)
            | Q(hash_externo__icontains=texto)
        )
    if fecha_desde:
        base_qs = base_qs.filter(fecha__gte=fecha_desde)
    if fecha_hasta:
        base_qs = base_qs.filter(fecha__lte=fecha_hasta)
    if estado:
        base_qs = base_qs.filter(estado=estado)
    if naturaleza:
        base_qs = base_qs.filter(naturaleza=naturaleza)
    if origen:
        base_qs = base_qs.filter(origen=origen)
    if banco:
        base_qs = base_qs.filter(producto__banco_id=banco)
    if producto:
        base_qs = base_qs.filter(producto_id=producto)
    if revisar == "1":
        base_qs = base_qs.filter(por_revisar=True)

    # La gráfica conserva todas las categorías del conjunto buscado para que,
    # después de hacer clic en un segmento, el usuario pueda elegir otro sin
    # perder el contexto. La categoría sólo restringe la tabla.
    chart_qs = base_qs
    if categoria:
        base_qs = base_qs.filter(categoria_id=categoria)

    categorias_chart_todas = [
        {"id": row["categoria_id"], "nombre": row["categoria__nombre"], "total": str(row["total"])}
        for row in chart_qs.filter(categoria__isnull=False)
        .values("categoria_id", "categoria__nombre")
        .annotate(total=Sum("monto_clp"))
        .filter(total__gt=0)
        .order_by("-total")
    ]
    categoria_chart = categorias_chart_todas[:8]
    if len(categorias_chart_todas) > 8:
        resto = sum((Decimal(row["total"]) for row in categorias_chart_todas[8:]), Decimal("0"))
        categoria_chart.append({"id": None, "nombre": "Otros", "total": str(resto)})

    qs = base_qs.select_related(
        "categoria", "producto", "producto__banco", "prestamo"
    ).prefetch_related(
        "detalles_cuentas_por_cobrar__cuenta",
        "conciliaciones_externas__evento__categoria",
        "conciliaciones_externas__evento__pago_neat",
        "conciliaciones_externas__evento__detalles_cuentas_por_cobrar__cuenta",
    )

    total_movimientos = qs.count()
    movimientos = list(qs[:300])
    for movimiento in movimientos:
        movimiento.cuenta_cobro_id = next(
            (
                detalle.cuenta_id
                for detalle in movimiento.detalles_cuentas_por_cobrar.all()
            ),
            None,
        )
        movimiento.detalles_neat = [
            {
                "descripcion": (
                    conciliacion.evento.pago_neat.destinatario
                    or conciliacion.evento.pago_neat.cuenta
                    or "NeatPagos"
                ),
                "categoria": conciliacion.evento.categoria,
                "monto": conciliacion.monto_asignado,
                "estado": conciliacion.evento.estado,
                "evento_id": conciliacion.evento_id,
                "cuenta_cobro_id": next(
                    (
                        detalle.cuenta_id
                        for detalle in conciliacion.evento.detalles_cuentas_por_cobrar.all()
                    ),
                    None,
                ),
            }
            for conciliacion in movimiento.conciliaciones_externas.all()
            if conciliacion.evento.fuente == "neat"
            and conciliacion.evento.estado not in {"FALLIDO", "REVERSADO"}
        ]

    context = {
        "movimientos": movimientos,
        "categorias": CategoriaTematica.objects.filter(user=user, activa=True),
        "bancos": Banco.objects.filter(productos__user=user).distinct(),
        "productos": Producto.objects.filter(user=user, activo=True, reemplazado_por__isnull=True).select_related("banco"),
        "prestamos": _prestamos_activos(user),
        "cuentas_por_cobrar": CuentaPorCobrar.objects.filter(
            user=user, activo=True
        ).order_by("nombre"),
        "bicicletas": _avance_bicicletas(user),
        "categoria_chart": categoria_chart,
        "total_movimientos": total_movimientos,
        "f_q": texto,
        "f_fecha_desde": request.GET.get("fecha_desde", ""),
        "f_fecha_hasta": request.GET.get("fecha_hasta", ""),
        "f_estado": estado or "",
        "f_naturaleza": naturaleza or "",
        "f_origen": origen or "",
        "f_categoria": categoria or "",
        "f_banco": banco or "",
        "f_producto": producto or "",
        "f_revisar": revisar or "",
        "ESTADOS": MovimientoBancario._meta.get_field("estado").choices,
        "NATURALEZAS": MovimientoBancario._meta.get_field("naturaleza").choices,
        "ORIGENES": MovimientoBancario._meta.get_field("origen").choices,
    }
    return render(request, "banca/movimientos.html", context)


@login_required
def banca_importar(request):
    """Carga masiva de PDFs y resumen auditable de lo que fue detectado."""
    user = request.user
    if request.method == "POST":
        archivos = request.FILES.getlist("archivos")
        if not archivos:
            messages.error(request, "Selecciona al menos un estado de cuenta PDF.")
            return redirect("banca_importar")

        procesados = []
        rechazados = 0
        duplicados = 0
        reanalizados = 0
        for archivo in archivos:
            nombre = (archivo.name or "").lower()
            if not nombre.endswith(".pdf"):
                rechazados += 1
                messages.error(request, f"{archivo.name}: solo se aceptan archivos PDF.")
                continue
            if archivo.size > 15 * 1024 * 1024:
                rechazados += 1
                messages.error(request, f"{archivo.name}: supera el límite de 15 MB.")
                continue
            resultado = importar_estado_cuenta(user, archivo, nombre_archivo=archivo.name)
            es_duplicado = bool(getattr(resultado, "fue_duplicado", False))
            if es_duplicado:
                duplicados += 1
                try:
                    resultado = reanalizar_importacion(user, resultado)
                except Exception as exc:
                    messages.error(
                        request,
                        f"{archivo.name}: ya estaba importado, pero no se pudo "
                        f"reanalizar con el lector actual: {exc}",
                    )
                    continue
                resultado.fue_duplicado = True
                reanalizados += 1
            procesados.append(resultado)

        importaciones_ok = [i for i in procesados if i.estado == "PROCESADO"]
        clasificacion = {"regla": 0, "ia": 0, "sin_resolver": 0}
        ids = list(
            MovimientoBancario.objects.filter(
                user=user, importaciones__in=importaciones_ok
            )
            .values_list("id", flat=True)
            .distinct()
        )
        if ids:
            reconciliacion.reconciliar(user, ids=ids)
            clasificacion = clasificar_pendientes(user, ids=ids)
            if clasificacion.get("error"):
                messages.error(
                    request,
                    f"La IA no pudo clasificar el lote: {clasificacion['error']}",
                )
        if importaciones_ok:
            nuevos = sum(i.operaciones_nuevas for i in importaciones_ok)
            messages.success(
                request,
                f"{len(importaciones_ok)} estado(s) procesado(s): {nuevos} movimiento(s) nuevo(s). "
                f"Clasificación automática: {clasificacion['ia']} por IA, "
                f"{clasificacion['regla']} por reglas aprendidas y "
                f"{clasificacion['sin_resolver']} pendiente(s).",
            )
            from banca.services.notificador import avisar_resumen_importacion

            for importacion in importaciones_ok:
                avisar_resumen_importacion(user, importacion)
        if rechazados:
            messages.warning(request, f"{rechazados} archivo(s) no se procesaron.")
        if reanalizados:
            messages.info(
                request,
                f"{reanalizados} archivo(s) ya estaban importados: se reanalizaron "
                "con el lector actual sin duplicar movimientos ni borrar ajustes manuales.",
            )
        elif duplicados:
            messages.info(
                request,
                f"{duplicados} archivo(s) ya estaban importados y no se duplicaron.",
            )
        return redirect("banca_importar")

    importaciones = list(
        ImportacionEstadoCuenta.objects.filter(user=user)
        .select_related("banco", "producto")[:20]
    )
    cfg = ConfiguracionBanca.objects.filter(user=user).first()
    seleccion = request.GET.get("importacion")
    seleccionada = None
    if seleccion:
        seleccionada = next(
            (item for item in importaciones if str(item.pk) == str(seleccion)), None
        )
    scope = (
        MovimientoBancario.objects.filter(user=user, importaciones=seleccionada)
        if seleccionada
        else MovimientoBancario.objects.filter(
            user=user, importaciones__in=importaciones
        )
    )
    movimientos_scope = scope.select_related("categoria", "producto").distinct()
    por_categoria = defaultdict(lambda: {"total": Decimal("0"), "n": 0})
    for mov in movimientos_scope.iterator():
        monto_analitica = mov.monto if mov.moneda == "USD" else mov.monto_clp
        if not mov.es_gasto_ocio and not (
            mov.contabiliza and mov.es_cargo and monto_analitica > 0
        ):
            continue
        categoria = mov.categoria.nombre if mov.categoria else "Sin categoría"
        clave = (categoria, mov.moneda)
        por_categoria[clave]["total"] += monto_analitica
        por_categoria[clave]["n"] += 1
    movimientos = list(movimientos_scope[:80])
    categorias = [
        {
            "nombre": clave[0],
            "moneda": clave[1],
            "total": valores["total"],
            "n": valores["n"],
        }
        for clave, valores in sorted(
            por_categoria.items(), key=lambda item: item[1]["total"], reverse=True
        )
    ]
    total_movimientos = scope.distinct().count()
    pendientes = scope.filter(categoria__isnull=True, naturaleza="OPERACIONAL").distinct().count()
    contexto = {
        "importaciones": importaciones,
        "seleccionada": seleccionada,
        "movimientos": movimientos,
        "categorias": categorias,
        "categoria_chart": [
            {
                "label": item["nombre"] + (" (USD)" if item["moneda"] == "USD" else ""),
                "total": float(item["total"]),
            }
            for item in categorias
        ],
        "total_movimientos": total_movimientos,
        "pendientes": pendientes,
        "bancos_detectados": len({i.banco_detectado for i in importaciones if i.banco_detectado}),
        "categorias_tematicas": CategoriaTematica.objects.filter(
            user=user, activa=True
        ),
        "mapeos_comercio": MapeoComercio.objects.filter(user=user).select_related(
            "categoria"
        )[:24],
        "ia_configurada": bool(cfg and cfg.ia_api_key_cifrada),
        "modelo_ia": cfg.modelo_ia if cfg else "",
    }
    return render(request, "banca/importar.html", contexto)


@login_required
@require_POST
def banca_recalcular_clasificaciones(request):
    """Recalcula IA/reglas en un estado o en todo el historial no manual."""
    scope = request.POST.get("scope", "importacion")
    ids = None
    if scope == "importacion":
        importacion = get_object_or_404(
            ImportacionEstadoCuenta,
            pk=request.POST.get("importacion_id"),
            user=request.user,
        )
        ids = list(importacion.movimientos.values_list("id", flat=True))
    resultado = recalcular_clasificaciones(request.user, ids=ids)
    from banca.services.notificador import avisar_limites_categoria, avisar_presupuesto

    avisar_limites_categoria(request.user)
    avisar_presupuesto(request.user)
    resumen = (
        "Categorías recalculadas: "
        f"{resultado['ia']} por IA, {resultado['regla']} por reglas, "
        f"{resultado['sin_resolver']} pendientes y "
        f"{resultado['manuales_protegidos']} manuales protegidas."
    )
    if resultado.get("error"):
        conservacion = (
            f" No se aplicaron cambios y se conservaron {resultado.get('restaurados', 0)} "
            "clasificaciones anteriores."
        )
        messages.error(
            request,
            f"{resumen}{conservacion} Motivo: {resultado['error']}",
        )
    else:
        messages.success(request, resumen)
    return redirect(request.META.get("HTTP_REFERER", "banca_importar"))


@login_required
@require_POST
def banca_reanalizar_importacion(request, pk):
    importacion = get_object_or_404(
        ImportacionEstadoCuenta, pk=pk, user=request.user
    )
    try:
        reanalizar_importacion(request.user, importacion)
    except Exception as exc:
        messages.error(request, f"No se pudo reanalizar el archivo: {exc}")
    else:
        messages.success(request, "Archivo reanalizado sin borrar decisiones manuales.")
    return redirect(f"{reverse('banca_importar')}?importacion={pk}")


def _prestamos_activos(user):
    try:
        from prestamos.models import Prestamo

        return list(Prestamo.objects.filter(user=user, activo=True).order_by("nombre"))
    except Exception:
        return []


def _avance_bicicletas(user):
    """Avance de cada préstamo: cuánto se ha vinculado (cargos) vs su monto total."""
    from django.db.models import Sum

    out = []
    for p in _prestamos_activos(user):
        total = p.monto_total or Decimal("0")
        cargos = MovimientoBancario.objects.filter(
            prestamo=p, monto_clp__gt=0
        ).exclude(estado="DESCARTADO_CIERRE").aggregate(s=Sum("monto_clp"))["s"] or Decimal("0")
        abonos = MovimientoBancario.objects.filter(
            prestamo=p, monto_clp__lt=0
        ).exclude(estado="DESCARTADO_CIERRE").aggregate(s=Sum("monto_clp"))["s"] or Decimal("0")
        pct = (cargos / total * 100) if total > 0 else Decimal("0")
        out.append(
            {
                "prestamo": p,
                "total": total,
                "vinculado": cargos,
                "retornado": -abonos,
                "pct": min(pct, Decimal("100")),
                "restante": max(total - cargos, Decimal("0")),
            }
        )
    return out


@login_required
@require_POST
def banca_reclasificar(request, pk):
    """Reclasificación manual: fija categoría y esencialidad, y aprende la regla."""
    mov = get_object_or_404(MovimientoBancario, pk=pk, user=request.user)
    cat_id = request.POST.get("categoria")
    esencialidad = request.POST.get("esencialidad")
    if cat_id:
        cat = get_object_or_404(CategoriaTematica, pk=cat_id, user=request.user)
        mov.categoria = cat
        mov.esencialidad = esencialidad or cat.esencialidad_defecto
        mov.fuente_clasificacion = "MANUAL"
        mov.categoria_es_manual = True
        mov.clasificado_at = timezone.now()
        mov.save()
        # aprender comercio -> categoría para el futuro
        comercio = request.POST.get("comercio") or mov.descripcion
        guardar_mapeo_comercio(request.user, comercio, cat, origen="MANUAL")
        messages.success(request, "Movimiento reclasificado y regla aprendida.")
    return redirect(request.META.get("HTTP_REFERER", "banca_movimientos"))


@login_required
@require_POST
def banca_confirmar_revision(request, pk):
    """Resuelve un abono 'por revisar': ingreso real (OPERACIONAL) o transferencia."""
    mov = get_object_or_404(MovimientoBancario, pk=pk, user=request.user)
    decision = request.POST.get("decision")
    mov.por_revisar = False
    mov.naturaleza = "TRANSFERENCIA" if decision == "transferencia" else "OPERACIONAL"
    mov.naturaleza_es_manual = True
    mov.save(
        update_fields=[
            "por_revisar",
            "naturaleza",
            "naturaleza_es_manual",
            "updated_at",
        ]
    )
    messages.success(request, "Revisión resuelta.")
    return redirect(request.META.get("HTTP_REFERER", "banca_movimientos"))


@login_required
@require_POST
def banca_asignar_prestamo(request, pk):
    """Clasifica financiamiento o asigna un préstamo; fija la decisión manual."""
    mov = get_object_or_404(MovimientoBancario, pk=pk, user=request.user)
    prestamo_id = request.POST.get("prestamo")
    if prestamo_id == "transferencia":
        categoria, _ = CategoriaTematica.objects.get_or_create(
            user=request.user,
            nombre="Financiamiento / Pago de TDC",
            defaults={
                "aplica_a": "EGRESO",
                "origen": "SISTEMA",
                "esencialidad_defecto": "FIJO",
            },
        )
        mov.prestamo = None
        mov.naturaleza = "TRANSFERENCIA"
        mov.categoria = categoria
        mov.fuente_clasificacion = "MANUAL"
        mov.categoria_es_manual = True
        mov.clasificado_at = timezone.now()
        guardar_mapeo_comercio(
            request.user, mov.descripcion, categoria, origen="MANUAL"
        )
        messages.success(
            request,
            "Marcado como pago de otra TDC: es financiamiento y no consumo.",
        )
    elif prestamo_id:
        from prestamos.models import Prestamo

        prestamo = get_object_or_404(Prestamo, pk=prestamo_id, user=request.user)
        mov.prestamo = prestamo
        mov.naturaleza = "TRANSFERENCIA"
        messages.success(request, f"Vinculado a «{prestamo.nombre}» (transferencia).")
    else:
        # "— gasto real —": desvincula y vuelve operacional
        mov.prestamo = None
        mov.naturaleza = "OPERACIONAL"
        messages.success(request, "Marcado como gasto/ingreso real (sin préstamo).")
    mov.por_revisar = False
    mov.naturaleza_es_manual = True
    mov.save(
        update_fields=[
            "prestamo",
            "naturaleza",
            "por_revisar",
            "naturaleza_es_manual",
            "categoria",
            "fuente_clasificacion",
            "categoria_es_manual",
            "clasificado_at",
            "updated_at",
        ]
    )
    return redirect(request.META.get("HTTP_REFERER", "banca_movimientos"))


def _asignar_cuenta_por_cobrar(request, fuente):
    from patrimonio.services.cuentas_por_cobrar import asignar_fuente_a_cuenta

    cuenta_id = request.POST.get("cuenta_cobro")
    cuenta = None
    if cuenta_id:
        cuenta = get_object_or_404(
            CuentaPorCobrar, pk=cuenta_id, user=request.user, activo=True
        )
    try:
        detalle = asignar_fuente_a_cuenta(fuente, cuenta)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        if detalle:
            messages.success(
                request,
                f"Vinculado a «{detalle.cuenta.nombre}»: queda fuera del consumo y aumenta la cuenta por cobrar.",
            )
        else:
            messages.success(request, "Desvinculado de cuentas por cobrar; vuelve a tratarse como consumo.")
    return redirect(request.META.get("HTTP_REFERER", "banca_movimientos"))


@login_required
@require_POST
def banca_asignar_cuenta_cobrar_movimiento(request, pk):
    movimiento = get_object_or_404(MovimientoBancario, pk=pk, user=request.user)
    return _asignar_cuenta_por_cobrar(request, movimiento)


@login_required
@require_POST
def banca_asignar_cuenta_cobrar_neat(request, pk):
    evento = get_object_or_404(
        EventoFinancieroExterno, pk=pk, user=request.user, fuente="neat"
    )
    return _asignar_cuenta_por_cobrar(request, evento)


@login_required
@require_POST
def banca_sync(request):
    """
    Ejecuta la sincronización inline y reporta el resultado de cada fuente.

    Se corre inline (no vía Celery) para que el botón funcione sin un worker en marcha; la
    ejecución programada en background llega con Celery Beat (Fase 4). Se puede acotar a una
    sola fuente con el parámetro POST `conector` (útil para refrescar solo cripto sin abrir
    el navegador del banco).
    """
    from banca.services.sync import run_sync

    modo = request.POST.get("modo", "full")
    conector = request.POST.get("conector")
    creds = CredencialBanco.objects.filter(user=request.user, activo=True)
    if conector:
        creds = creds.filter(conector=conector)
    if not creds.exists():
        messages.error(
            request, "No tienes fuentes configuradas (agrégalas en Configuración)."
        )
        return redirect("banca_dashboard")

    ok, errores = 0, []
    for c in creds:
        log = run_sync(c, modo=modo, headless=True)
        if log.exito:
            ok += 1
        else:
            errores.append(f"{c.banco.nombre}: {log.mensaje}")

    if ok:
        messages.success(request, f"Sincronización lista para {ok} fuente(s).")
    for e in errores:
        messages.error(request, f"Error — {e}")
    if errores:
        messages.info(request, "Revisa el detalle en Banca → Sync.")
    return redirect("banca_dashboard")


@login_required
def banca_fuentes_externas(request):
    """Centro de operación read-only para Houm y NeatPagos."""
    from departamentos.models import Departamento

    credentials = list(
        CredencialBanco.objects.filter(
            user=request.user, conector__in=("houm", "neat")
        ).select_related("banco")
    )
    events = list(
        EventoFinancieroExterno.objects.filter(user=request.user)
        .select_related("pago_neat", "liquidacion_houm", "liquidacion_houm__vinculo_propiedad")[:100]
    )
    pending_properties = (
        LiquidacionHoum.objects.filter(evento__user=request.user, vinculo_propiedad__isnull=True)
        .values("external_property_id", "propiedad_nombre")
        .distinct()
    )
    context = {
        "credenciales_externas": credentials,
        "eventos_externos": events,
        "importaciones_externas": ImportacionFuenteExterna.objects.filter(user=request.user)[:30],
        "pendientes_propiedad": pending_properties,
        "departamentos_disponibles": Departamento.objects.filter(user=request.user, activo=True),
        "vinculos_propiedad": VinculoPropiedadExterna.objects.filter(user=request.user).select_related("departamento"),
    }
    return render(request, "banca/fuentes_externas.html", context)


@login_required
@require_POST
def banca_importar_fuente_externa(request):
    from banca.services.fuentes_externas import importar_archivo_externo

    source = request.POST.get("fuente", "")
    uploaded = request.FILES.get("archivo")
    if not uploaded:
        messages.error(request, "Selecciona un archivo XLSX, CSV o JSON.")
        return redirect("banca_fuentes_externas")
    try:
        record, duplicate = importar_archivo_externo(request.user, source, uploaded)
    except Exception as exc:
        messages.error(request, f"No se pudo importar: {exc}")
        return redirect("banca_fuentes_externas")
    if duplicate:
        messages.info(request, "Ese archivo ya había sido importado; no se duplicaron datos.")
    elif record.estado == "PROCESADO":
        messages.success(
            request,
            f"{record.get_fuente_display()}: {record.elementos_detectados} elemento(s), "
            f"{record.nuevos} nuevo(s) y {record.actualizados} actualizado(s).",
        )
    else:
        messages.error(request, f"El archivo no pudo procesarse: {record.mensaje}")
    return redirect("banca_fuentes_externas")


@login_required
def banca_descargar_importacion_externa(request, pk):
    record = get_object_or_404(ImportacionFuenteExterna, pk=pk, user=request.user)
    if not record.contenido_privado:
        messages.error(request, "El archivo original no está disponible.")
        return redirect("banca_fuentes_externas")
    response = HttpResponse(bytes(record.contenido_privado), content_type=record.mime_type or "application/octet-stream")
    safe_name = record.nombre_archivo.replace('"', "").replace("\n", "")
    response["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _dispatch_external_sync(credential):
    from banca.tasks import sync_fuente_externa_task

    try:
        sync_fuente_externa_task.delay(credential.pk, manual=True)
        return True
    except Exception:
        from banca.services.fuentes_externas import run_external_sync

        run_external_sync(credential, manual=True)
        return False


@login_required
@require_POST
def banca_sync_fuente_externa(request, pk):
    credential = get_object_or_404(
        CredencialBanco, pk=pk, user=request.user, conector__in=("houm", "neat"), activo=True
    )
    if credential.sincronizacion_en_curso:
        messages.info(request, f"{credential.banco.nombre} ya se está sincronizando.")
    else:
        background = _dispatch_external_sync(credential)
        messages.success(
            request,
            "Sincronización enviada a segundo plano." if background else "Sincronización ejecutada localmente.",
        )
    return redirect("banca_fuentes_externas")


@login_required
@require_POST
def banca_sync_fuentes_externas(request):
    credentials = CredencialBanco.objects.filter(
        user=request.user, conector__in=("houm", "neat"), activo=True
    )
    count = 0
    for credential in credentials:
        if not credential.sincronizacion_en_curso:
            _dispatch_external_sync(credential)
            count += 1
    messages.success(request, f"Sincronización solicitada para {count} fuente(s).")
    return redirect("banca_fuentes_externas")


@login_required
@require_POST
def banca_configurar_fuente_externa(request, pk):
    credential = get_object_or_404(
        CredencialBanco, pk=pk, user=request.user, conector__in=("houm", "neat")
    )
    form = ProgramacionFuenteForm(request.POST, instance=credential)
    if form.is_valid():
        form.save()
        messages.success(request, f"Programación de {credential.banco.nombre} actualizada.")
    else:
        for errors in form.errors.values():
            messages.error(request, errors.as_text())
    return redirect("banca_fuentes_externas")


@login_required
@require_POST
def banca_probar_fuente_externa(request, pk):
    from banca.services.fuentes_externas import probar_conexion

    credential = get_object_or_404(
        CredencialBanco, pk=pk, user=request.user, conector__in=("houm", "neat")
    )
    try:
        count = probar_conexion(credential)
    except Exception as exc:
        messages.error(request, f"No fue posible validar la conexión: {exc}")
    else:
        messages.success(request, f"Conexión de solo lectura validada ({count} elemento(s) visibles).")
    return redirect("banca_fuentes_externas")


@login_required
@require_POST
def banca_vincular_propiedad_externa(request):
    from departamentos.models import Departamento
    from banca.services.fuentes_externas import vincular_propiedad

    property_id = request.POST.get("external_property_id", "").strip()
    department = get_object_or_404(
        Departamento, pk=request.POST.get("departamento"), user=request.user
    )
    vincular_propiedad(request.user, property_id, department)
    messages.success(request, f"Propiedad Houm vinculada con {department.codigo}.")
    return redirect("banca_fuentes_externas")


# ---------------------------------------------------------------- Deudas (saldos)
@login_required
def banca_deudas(request):
    """Último SaldoSnapshot por producto: tarjetas (cupo CLP/USD) y créditos/hipotecario."""
    user = request.user
    prod_ids = (
        SaldoSnapshot.objects.filter(user=user)
        .values_list("producto", flat=True)
        .distinct()
    )
    tarjetas, creditos = [], []
    for pid in prod_ids:
        snap = (
            SaldoSnapshot.objects.filter(user=user, producto_id=pid)
            .select_related("producto", "producto__banco")
            .order_by("-fecha")
            .first()
        )
        if not snap:
            continue
        if snap.producto.tipo == "TDC":
            snap.cupo_utilizado_mostrar = snap.cupo_utilizado_clp
            if (
                snap.cupo_utilizado_mostrar is None
                and snap.cupo_total_clp is not None
                and snap.cupo_disponible_clp is not None
            ):
                snap.cupo_utilizado_mostrar = max(
                    snap.cupo_total_clp - snap.cupo_disponible_clp,
                    Decimal("0"),
                )
            tarjetas.append(snap)
        else:
            creditos.append(snap)

    context = {
        "tarjetas": tarjetas,
        "creditos": creditos,
        "total_deuda": sum(
            (s.deuda_total_clp for s in tarjetas + creditos), Decimal("0")
        ),
    }
    return render(request, "banca/deudas.html", context)


# ---------------------------------------------------------------- Categorías temáticas
@login_required
def banca_categorias(request):
    user = request.user
    if request.method == "POST":
        form = CategoriaTematicaForm(request.POST)
        if form.is_valid():
            cat = form.save(commit=False)
            cat.user = user
            try:
                cat.save()
                messages.success(request, f"Categoría «{cat.nombre}» creada.")
            except Exception:
                messages.error(request, "Ya tienes una categoría con ese nombre.")
            return redirect("banca_categorias")
        messages.error(request, "Revisa los datos de la categoría.")
    else:
        form = CategoriaTematicaForm()

    context = {
        "categorias": CategoriaTematica.objects.filter(user=user),
        "form": form,
        "ESENCIALIDAD": CategoriaTematica._meta.get_field(
            "esencialidad_defecto"
        ).choices,
    }
    return render(request, "banca/categorias.html", context)


@login_required
@require_POST
def banca_categoria_editar(request, pk):
    cat = get_object_or_404(CategoriaTematica, pk=pk, user=request.user)
    form = CategoriaTematicaForm(request.POST, instance=cat)
    if form.is_valid():
        form.save()
        messages.success(request, "Categoría actualizada.")
    else:
        messages.error(request, "No se pudo actualizar la categoría.")
    return redirect("banca_categorias")


@login_required
@require_POST
def banca_categoria_borrar(request, pk):
    cat = get_object_or_404(CategoriaTematica, pk=pk, user=request.user)
    nombre = cat.nombre
    cat.delete()
    messages.success(request, f"Categoría «{nombre}» eliminada.")
    return redirect("banca_categorias")


@login_required
@require_POST
def banca_sembrar_categorias(request):
    """Ejecuta el seed de categorías por defecto para el usuario actual."""
    from banca.management.commands.seed_categorias_banca import sembrar

    creadas = sembrar(request.user)
    messages.success(request, f"Semilla aplicada: {creadas} categorías nuevas.")
    return redirect("banca_categorias")


# ---------------------------------------------------------------- Configuración
@login_required
def banca_config(request):
    from banca.forms import listar_conectores

    user = request.user
    cfg, _ = ConfiguracionBanca.objects.get_or_create(user=user)
    context = {
        "credenciales": CredencialBanco.objects.filter(user=user).select_related(
            "banco"
        ),
        "cred_form": CredencialBancoForm(),
        "conectores": listar_conectores(),
        "param_form": ConfiguracionBancaForm(instance=cfg),
        "cfg": cfg,
        "tiene_ia_key": bool(cfg.ia_api_key_cifrada),
        "contrapartes": ContraparteConocida.objects.filter(user=user).select_related(
            "prestamo"
        ),
        "contraparte_form": ContraparteConocidaForm(user=user),
    }
    return render(request, "banca/configuracion.html", context)


@login_required
@require_POST
def banca_guardar_credencial(request):
    cred_id = request.POST.get("cred_id")
    instance = None
    if cred_id:
        instance = get_object_or_404(CredencialBanco, pk=cred_id, user=request.user)
    form = CredencialBancoForm(request.POST, instance=instance)
    if not form.is_valid():
        for err in form.errors.values():
            messages.error(request, err.as_text())
        return redirect("banca_config")
    try:
        cred = form.save(commit=False)
        cred.user = request.user
        cred.save()
        messages.success(request, "Credencial guardada (cifrada).")
    except RuntimeError as e:
        # crypto sin BANCA_FERNET_KEY u otro problema de cifrado
        messages.error(request, str(e))
    except Exception:
        messages.error(request, "Ya existe una credencial para ese banco.")
    return redirect("banca_config")


@login_required
@require_POST
def banca_borrar_credencial(request, pk):
    cred = get_object_or_404(CredencialBanco, pk=pk, user=request.user)
    cred.delete()
    messages.success(request, "Credencial eliminada.")
    return redirect("banca_config")


@login_required
@require_POST
def banca_guardar_parametros(request):
    cfg, _ = ConfiguracionBanca.objects.get_or_create(user=request.user)
    form = ConfiguracionBancaForm(request.POST, instance=cfg)
    if form.is_valid():
        form.save()
        messages.success(request, "Parámetros de banca actualizados.")
    else:
        messages.error(request, "Revisa los parámetros ingresados.")
    return redirect("banca_config")


@login_required
@require_POST
def banca_refrescar_modelos(request):
    """Consulta /models del proveedor de IA y cachea la lista en ConfiguracionBanca."""
    from banca.services.clasificacion_ia import listar_modelos

    cfg, _ = ConfiguracionBanca.objects.get_or_create(user=request.user)
    try:
        api_key = cfg.get_ia_api_key()
    except RuntimeError as e:
        messages.error(request, str(e))
        return redirect("banca_config")
    if not api_key:
        messages.error(request, "Primero guarda la API key de IA en los parámetros.")
        return redirect("banca_config")
    try:
        modelos = listar_modelos(cfg.ia_base_url, api_key)
    except Exception as e:
        messages.error(request, f"No se pudieron obtener los modelos: {e}")
        return redirect("banca_config")
    if not modelos:
        messages.error(request, "El proveedor no devolvió modelos.")
        return redirect("banca_config")
    cfg.modelos_disponibles = modelos
    cfg.save(update_fields=["modelos_disponibles", "updated_at"])
    messages.success(request, f"{len(modelos)} modelos disponibles actualizados.")
    return redirect("banca_config")


@login_required
@require_POST
def banca_guardar_contraparte(request):
    cp_id = request.POST.get("cp_id")
    instance = None
    if cp_id:
        instance = get_object_or_404(ContraparteConocida, pk=cp_id, user=request.user)
    form = ContraparteConocidaForm(request.POST, instance=instance, user=request.user)
    if form.is_valid():
        cp = form.save(commit=False)
        cp.user = request.user
        cp.save()
        messages.success(request, "Contraparte guardada.")
    else:
        messages.error(request, "Revisa los datos de la contraparte.")
    return redirect("banca_config")


@login_required
@require_POST
def banca_borrar_contraparte(request, pk):
    cp = get_object_or_404(ContraparteConocida, pk=pk, user=request.user)
    cp.delete()
    messages.success(request, "Contraparte eliminada.")
    return redirect("banca_config")


# ---------------------------------------------------------------- Historial de sync
@login_required
def banca_sync_logs(request):
    logs = (
        SyncLog.objects.filter(user=request.user)
        .select_related("banco")
        .order_by("-iniciado")[:50]
    )
    return render(request, "banca/sync_logs.html", {"logs": logs})
