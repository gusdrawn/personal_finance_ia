from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from openpyxl import Workbook
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from banca.models import CategoriaTematica, ImportacionEstadoCuenta, LimiteCategoria, MapeoComercio, MovimientoBancario
from gastos.models import GastoProgramado, MovimientoManual
from gastos.serializers import CategoriaTematicaSerializer, GastoProgramadoSerializer, MovimientoManualSerializer
from gastos.services import primer_dia, resumen_gastos


def _periodos(request):
    hoy = date.today()
    desde_texto = request.query_params.get("desde", "")
    hasta_texto = request.query_params.get("hasta", "")
    try:
        desde = parse_date(desde_texto + "-01") if len(desde_texto) == 7 else None
        hasta = parse_date(hasta_texto + "-01") if len(hasta_texto) == 7 else None
    except ValueError as exc:
        raise ValueError("El rango mensual no es válido.") from exc
    year = request.query_params.get("year")
    month = request.query_params.get("mes") or request.query_params.get("month")
    if year and month:
        try:
            desde = hasta = date(int(year), int(month), 1)
        except (TypeError, ValueError):
            raise ValueError("El año o mes no es válido.")
    desde = primer_dia(desde or hoy)
    hasta = primer_dia(hasta or desde)
    return (hasta, desde) if hasta < desde else (desde, hasta)


def _filtros(request):
    return {key: request.query_params.get(key) for key in (
        "categoria", "banco", "producto", "direccion", "fuente", "esencialidad", "estado"
    )}


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    return value


class CategoriaTematicaViewSet(viewsets.ModelViewSet):
    serializer_class = CategoriaTematicaSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return CategoriaTematica.objects.filter(user=self.request.user).order_by("aplica_a", "orden", "nombre")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, origen="USUARIO")

    @action(detail=True, methods=["post"])
    def fusionar(self, request, pk=None):
        origen = self.get_object()
        destino = get_object_or_404(CategoriaTematica, pk=request.data.get("destino"), user=request.user)
        if origen.pk == destino.pk:
            return Response({"detail": "Elige una categoría diferente."}, status=400)
        with transaction.atomic():
            MovimientoBancario.objects.filter(user=request.user, categoria=origen).update(categoria=destino)
            for manual in MovimientoManual.objects.filter(user=request.user, categoria=origen):
                conflicto = None
                if manual.es_resumen_mensual:
                    conflicto = MovimientoManual.objects.filter(
                        user=request.user, categoria=destino, periodo=manual.periodo,
                        direccion=manual.direccion, es_resumen_mensual=True,
                    ).first()
                if conflicto:
                    conflicto.monto += manual.monto
                    conflicto.monto_clp += manual.monto_clp
                    conflicto.notas = "\n".join(filter(None, [conflicto.notas, manual.notas]))
                    conflicto.save(update_fields=["monto", "monto_clp", "notas", "updated_at"])
                    manual.delete()
                else:
                    manual.categoria = destino
                    manual.save(update_fields=["categoria", "updated_at"])
            origen.eventos_externos.filter(user=request.user).update(categoria=destino)
            for mapeo in MapeoComercio.objects.filter(user=request.user, categoria=origen):
                MapeoComercio.objects.update_or_create(
                    user=request.user, patron=mapeo.patron,
                    defaults={"categoria": destino, "origen": mapeo.origen, "hits": mapeo.hits},
                )
            MapeoComercio.objects.filter(user=request.user, categoria=origen).delete()
            for limite in LimiteCategoria.objects.filter(user=request.user, categoria=origen):
                existente = LimiteCategoria.objects.filter(user=request.user, categoria=destino).first()
                if existente:
                    existente.monto_mensual_clp += limite.monto_mensual_clp
                    existente.save(update_fields=["monto_mensual_clp", "updated_at"])
                    limite.delete()
                else:
                    limite.categoria = destino
                    limite.save(update_fields=["categoria", "updated_at"])
            origen.activa = False
            origen.save(update_fields=["activa"])
        return Response({"fusionada": origen.pk, "destino": destino.pk})


class MovimientoManualViewSet(viewsets.ModelViewSet):
    serializer_class = MovimientoManualSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return MovimientoManual.objects.filter(user=self.request.user).select_related("categoria", "departamento")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=["post"])
    def carga_mensual(self, request):
        periodo = parse_date(str(request.data.get("periodo", "")) + "-01")
        if not periodo:
            return Response({"periodo": "Usa el formato YYYY-MM."}, status=400)
        proximo = date(periodo.year + (periodo.month == 12), 1 if periodo.month == 12 else periodo.month + 1, 1)
        ultimo = proximo - timedelta(days=1)
        resultados = []
        with transaction.atomic():
            for item in request.data.get("movimientos", []):
                categoria = get_object_or_404(CategoriaTematica, pk=item.get("categoria"), user=request.user, activa=True)
                payload = {
                    "fecha": ultimo, "periodo": periodo,
                    "direccion": item.get("direccion", "EGRESO"), "monto": item.get("monto"),
                    "moneda": item.get("moneda", "CLP"), "categoria": categoria.pk,
                    "naturaleza": item.get("naturaleza", "OPERACIONAL"),
                    "descripcion": item.get("descripcion") or categoria.nombre,
                    "notas": item.get("notas", ""), "es_resumen_mensual": True,
                }
                existente = MovimientoManual.objects.filter(
                    user=request.user, periodo=periodo, categoria=categoria,
                    direccion=payload["direccion"], es_resumen_mensual=True,
                ).first()
                serializer = self.get_serializer(existente, data=payload)
                serializer.is_valid(raise_exception=True)
                resultados.append(serializer.save(user=request.user).pk)
        return Response({"ids": resultados, "count": len(resultados)}, status=status.HTTP_201_CREATED)


class GastoProgramadoViewSet(viewsets.ModelViewSet):
    serializer_class = GastoProgramadoSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return GastoProgramado.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=["get"])
    def total_ahorro_mensual(self, request):
        total = sum((g.ahorro_mensual for g in self.get_queryset().filter(activo=True)), Decimal("0"))
        return Response({"total_ahorro_mensual_clp": str(total)})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def gastos_resumen(request):
    try:
        desde, hasta = _periodos(request)
    except ValueError as exc:
        return Response({"periodo": str(exc)}, status=400)
    perspectiva = request.query_params.get("perspectiva", "consumo")
    if perspectiva not in {"caja", "consumo"}:
        return Response({"perspectiva": "Usa caja o consumo."}, status=400)
    return Response(_json_value(resumen_gastos(request.user, desde, hasta, perspectiva, _filtros(request))))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def gastos_detalle(request):
    try:
        desde, hasta = _periodos(request)
    except ValueError as exc:
        return Response({"periodo": str(exc)}, status=400)
    resultado = resumen_gastos(request.user, desde, hasta, request.query_params.get("perspectiva", "consumo"), _filtros(request))
    lineas = [linea for grupo in resultado["categorias"] for linea in grupo["movimientos"]]
    return Response(_json_value({"cantidad": len(lineas), "movimientos": lineas}))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def registrar_pago_real(request, pk):
    importacion = get_object_or_404(ImportacionEstadoCuenta, pk=pk, user=request.user, estado="PROCESADO")
    try:
        monto = Decimal(str(request.data.get("monto_clp", "")))
    except (InvalidOperation, TypeError):
        return Response({"monto_clp": "Monto inválido."}, status=400)
    if monto <= 0:
        return Response({"monto_clp": "El monto debe ser mayor que cero."}, status=400)
    importacion.monto_pagado_clp = monto
    fecha_pago = request.data.get("fecha_pago") or ""
    importacion.fecha_pago_real = parse_date(fecha_pago) if fecha_pago else None
    importacion.save(update_fields=["monto_pagado_clp", "fecha_pago_real"])
    return Response({"id": importacion.pk, "monto_pagado_clp": str(monto)})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def exportar_gastos(request):
    try:
        desde, hasta = _periodos(request)
    except ValueError as exc:
        return HttpResponse(str(exc), status=400, content_type="text/plain")
    resultado = resumen_gastos(request.user, desde, hasta, request.query_params.get("perspectiva", "consumo"), _filtros(request))
    wb = Workbook()
    ws = wb.active
    ws.title = "Gastos"
    ws.append(["Fecha", "Dirección", "Categoría", "Descripción", "Fuente", "Banco", "Producto", "Monto CLP", "Estado"])
    for grupo in resultado["categorias"]:
        for item in grupo["movimientos"]:
            ws.append([item["fecha"], item["direccion"], item["categoria"], item["descripcion"], item["fuente"], item["banco"], item["producto"], item["monto_clp"], item["estado"]])
    output = BytesIO()
    wb.save(output)
    response = HttpResponse(output.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="gastos-filtrados.xlsx"'
    return response
