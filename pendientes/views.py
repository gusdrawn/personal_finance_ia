from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from banca.models import MovimientoBancario
from departamentos.models import Departamento

from .models import OcurrenciaPendiente, ReglaPendiente
from .serializers import OcurrenciaPendienteSerializer, ReglaPendienteSerializer
from .services.calendario import materializar_usuario
from .services.generador import asegurar_reglas_automaticas
from .services.pagos import resolver_ocurrencia
from .services.resumen import resumen_propiedad


def _parse_date(value, fallback=None):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date() if value else fallback
    except (TypeError, ValueError):
        return fallback


@login_required
def dashboard_pendientes(request):
    asegurar_reglas_automaticas(request.user)
    materializar_usuario(request.user)
    qs = OcurrenciaPendiente.objects.filter(regla__user=request.user, regla__activa=True).select_related("regla", "regla__departamento", "regla__producto")
    filtro = request.GET.get("tipo")
    if filtro:
        qs = qs.filter(regla__tipo=filtro)
    propiedad = request.GET.get("departamento")
    if propiedad and propiedad.isdigit():
        qs = qs.filter(regla__departamento_id=propiedad)
    configurar_departamento_id = request.GET.get("configurar")
    if not (configurar_departamento_id and configurar_departamento_id.isdigit()):
        configurar_departamento_id = ""
    pendientes = qs.filter(estado="PENDIENTE").order_by("fecha_vencimiento", "regla__nombre")
    resueltas_recientes = qs.exclude(estado="PENDIENTE").order_by("-completada_at")[:12]
    propiedades = Departamento.objects.filter(user=request.user, activo=True).order_by("codigo")
    return render(request, "pendientes/dashboard.html", {
        "pendientes": pendientes,
        "vencidas": pendientes.filter(fecha_vencimiento__lt=date.today()),
        "proximas": pendientes.filter(fecha_vencimiento__gte=date.today()),
        "documentos": pendientes.filter(regla__tipo="DOCUMENTO"),
        "sincronizaciones": pendientes.filter(regla__tipo="SINCRONIZACION"),
        "resueltas_recientes": resueltas_recientes,
        "today": date.today(),
        "propiedades": propiedades,
        "tipos": [(key, label) for key, label in (("PAGO", "Pagos"), ("DOCUMENTO", "Documentos"), ("SINCRONIZACION", "Sincronizaciones"), ("MANUAL", "Manuales"))],
        "movimientos_recientes": MovimientoBancario.objects.filter(user=request.user).order_by("-fecha", "-id")[:30],
        "configurar_departamento_id": configurar_departamento_id,
    })


@login_required
@require_http_methods(["POST"])
def crear_tarea_manual(request):
    fecha = _parse_date(request.POST.get("fecha_inicio"), date.today())
    regla = ReglaPendiente.objects.create(
        user=request.user,
        nombre=(request.POST.get("nombre") or "Tarea pendiente").strip()[:180],
        descripcion=request.POST.get("descripcion", ""),
        tipo=request.POST.get("tipo", "MANUAL"),
        periodicidad="UNICA",
        fecha_inicio=fecha,
        monto_estimado=Decimal(request.POST["monto_estimado"]) if request.POST.get("monto_estimado") else None,
        contabiliza_gasto=False,
    )
    materializar_usuario(request.user)
    messages.success(request, "Tarea agregada a pendientes.")
    return redirect("dashboard_pendientes")


@login_required
@require_http_methods(["POST"])
def configurar_propiedad(request, departamento_id=None):
    if departamento_id is None:
        departamento_id = request.POST.get("departamento_id")
        if not departamento_id or not str(departamento_id).isdigit():
            messages.error(request, "Selecciona una propiedad para configurar la obligación.")
            return redirect("dashboard_pendientes")
    departamento = get_object_or_404(Departamento, pk=departamento_id, user=request.user)
    departamento.comuna = (request.POST.get("comuna") or departamento.comuna).strip()
    departamento.rol_sii = (request.POST.get("rol_sii") or departamento.rol_sii).strip()
    departamento.url_pago_aseo = (request.POST.get("url_pago_aseo") or departamento.url_pago_aseo).strip()
    departamento.save(update_fields=["comuna", "rol_sii", "url_pago_aseo", "updated_at"])
    tipo = request.POST.get("obligacion", "ASEO")
    es_aseo = tipo == "ASEO"
    nombre = "Aseo" if es_aseo else "Contribuciones"
    key = f"{tipo.lower()}:{departamento.id}"
    fecha = _parse_date(request.POST.get("fecha_inicio"))
    if not fecha:
        messages.error(request, "Indica la primera fecha del vencimiento.")
        return redirect("dashboard_pendientes")
    periodicidad = request.POST.get("periodicidad", "TRIMESTRAL" if es_aseo else "MESES")
    meses = [int(x) for x in request.POST.getlist("meses_vencimiento") if x.isdigit()]
    if not meses and not es_aseo:
        meses = [4, 6, 9, 11]
    regla, _ = ReglaPendiente.objects.update_or_create(
        user=request.user,
        clave_fuente=key,
        defaults={
            "nombre": f"{nombre} · {departamento.codigo}",
            "tipo": "PAGO",
            "departamento": departamento,
            "periodicidad": periodicidad,
            "fecha_inicio": fecha,
            "dia_vencimiento": int(request.POST.get("dia_vencimiento") or fecha.day),
            "meses_vencimiento": meses,
            "monto_estimado": Decimal(request.POST["monto_estimado"]) if request.POST.get("monto_estimado") else None,
            "url_pago": request.POST.get("url_pago") or departamento.url_pago_aseo if es_aseo else request.POST.get("url_pago", ""),
            "canal_cobro": request.POST.get("canal_cobro", "DIRECTO"),
            "recordar_dias": int(request.POST.get("recordar_dias") or 3),
            "contabiliza_gasto": True,
            "automatica": False,
            "activa": True,
        },
    )
    materializar_usuario(request.user)
    messages.success(request, f"Regla de {nombre.lower()} configurada para {departamento.codigo}.")
    return redirect("dashboard_pendientes")


@login_required
@require_http_methods(["POST"])
def resolver_pendiente(request, pk):
    ocurrencia = get_object_or_404(OcurrenciaPendiente.objects.select_related("regla"), pk=pk, regla__user=request.user)
    estado = request.POST.get("estado", "COMPLETADA")
    movimiento = None
    movimiento_id = request.POST.get("movimiento_bancario_id")
    if movimiento_id and movimiento_id.isdigit():
        movimiento = get_object_or_404(MovimientoBancario, pk=movimiento_id, user=request.user)
    try:
        monto = Decimal(request.POST["monto"]) if request.POST.get("monto") else None
        resolver_ocurrencia(
            ocurrencia,
            estado=estado,
            monto=monto,
            fecha_real=_parse_date(request.POST.get("fecha_real"), date.today()),
            movimiento_bancario=movimiento,
            nota=request.POST.get("nota", ""),
            referencia_pago=request.POST.get("referencia_pago", ""),
        )
        messages.success(request, "Pendiente actualizado.")
    except (ValueError, InvalidOperation) as exc:
        messages.error(request, str(exc))
    return redirect(request.POST.get("next") or "dashboard_pendientes")


class PendientesAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        asegurar_reglas_automaticas(request.user)
        materializar_usuario(request.user)
        qs = OcurrenciaPendiente.objects.filter(regla__user=request.user).select_related("regla", "regla__departamento")
        return Response(OcurrenciaPendienteSerializer(qs, many=True).data)

    def post(self, request):
        serializer = ReglaPendienteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        regla = serializer.save(user=request.user, automatica=False)
        materializar_usuario(request.user)
        return Response(ReglaPendienteSerializer(regla, context={"request": request}).data, status=201)


class PendienteDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, pk):
        ocurrencia = get_object_or_404(OcurrenciaPendiente, pk=pk, regla__user=request.user)
        serializer = OcurrenciaPendienteSerializer(ocurrencia, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
