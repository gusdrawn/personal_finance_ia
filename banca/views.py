"""Vistas de banca: dashboard de presupuesto/deudas y bandeja de movimientos."""

from __future__ import annotations

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from banca.forms import (
    CategoriaTematicaForm,
    ConfiguracionBancaForm,
    ContraparteConocidaForm,
    CredencialBancoForm,
)
from banca.models import (
    CategoriaTematica,
    ConfiguracionBanca,
    ContraparteConocida,
    CredencialBanco,
    MovimientoBancario,
    SaldoSnapshot,
    SyncLog,
)
from banca.services import analisis, presupuesto


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


@login_required
def banca_movimientos(request):
    user = request.user
    qs = MovimientoBancario.objects.filter(user=user).select_related(
        "categoria", "producto", "prestamo"
    )

    estado = request.GET.get("estado")
    naturaleza = request.GET.get("naturaleza")
    revisar = request.GET.get("revisar")
    if estado:
        qs = qs.filter(estado=estado)
    if naturaleza:
        qs = qs.filter(naturaleza=naturaleza)
    if revisar == "1":
        qs = qs.filter(por_revisar=True)

    context = {
        "movimientos": qs[:300],
        "categorias": CategoriaTematica.objects.filter(user=user, activa=True),
        "prestamos": _prestamos_activos(user),
        "bicicletas": _avance_bicicletas(user),
        "f_estado": estado or "",
        "f_naturaleza": naturaleza or "",
        "f_revisar": revisar or "",
        "ESTADOS": MovimientoBancario._meta.get_field("estado").choices,
        "NATURALEZAS": MovimientoBancario._meta.get_field("naturaleza").choices,
    }
    return render(request, "banca/movimientos.html", context)


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
        ).aggregate(s=Sum("monto_clp"))["s"] or Decimal("0")
        abonos = MovimientoBancario.objects.filter(
            prestamo=p, monto_clp__lt=0
        ).aggregate(s=Sum("monto_clp"))["s"] or Decimal("0")
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
        from banca.models import MapeoComercio
        from banca.services.clasificacion_ia import normalizar_glosa

        patron = normalizar_glosa(mov.descripcion)
        if patron:
            MapeoComercio.objects.update_or_create(
                user=request.user,
                patron=patron,
                defaults={"categoria": cat, "origen": "MANUAL"},
            )
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
    """Asigna (o quita) un préstamo a un movimiento; fija la naturaleza a mano."""
    mov = get_object_or_404(MovimientoBancario, pk=pk, user=request.user)
    prestamo_id = request.POST.get("prestamo")
    if prestamo_id:
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
            "updated_at",
        ]
    )
    return redirect(request.META.get("HTTP_REFERER", "banca_movimientos"))


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
