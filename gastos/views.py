from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q
from decimal import Decimal

from .models import CategoriaIngreso, RegistroMensual, GastoAnual, GastoTrimestral
from .serializers import (
    CategoriaIngresoSerializer, RegistroMensualSerializer,
    GastoAnualSerializer, GastoTrimestralSerializer
)


class CategoriaIngresoViewSet(viewsets.ModelViewSet):
    """API for expense/income categories"""
    serializer_class = CategoriaIngresoSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['nombre']
    ordering_fields = ['tipo', 'orden', 'nombre']
    ordering = ['tipo', 'orden']

    def get_queryset(self):
        return CategoriaIngreso.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class RegistroMensualViewSet(viewsets.ModelViewSet):
    """API for monthly records"""
    serializer_class = RegistroMensualSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    filterset_fields = ['year', 'mes', 'tipo', 'moneda']
    search_fields = ['categoria__nombre']

    def get_queryset(self):
        return RegistroMensual.objects.filter(user=self.request.user).select_related('categoria')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=['get'])
    def by_month(self, request):
        """Get all records for a specific month with totals"""
        year = request.query_params.get('year')
        mes = request.query_params.get('mes')
        
        if not year or not mes:
            return Response({'error': 'year and mes parameters required'}, status=status.HTTP_400_BAD_REQUEST)
        
        registros = RegistroMensual.objects.filter(
            user=request.user,
            year=int(year),
            mes=int(mes)
        ).select_related('categoria').order_by('categoria__tipo', 'categoria__orden')
        
        # Group by category type
        grouped = {}
        for registro in registros:
            tipo = registro.categoria.get_tipo_display()
            if tipo not in grouped:
                grouped[tipo] = []
            grouped[tipo].append({
                'id': registro.id,
                'categoria_id': registro.categoria.id,
                'categoria': registro.categoria.nombre,
                'monto': str(registro.monto),
                'moneda': registro.moneda,
                'tipo': registro.tipo
            })
        
        # Calculate totals
        totales = {}
        for tipo, items in grouped.items():
            total_clp = sum(Decimal(item['monto']) for item in items if item['moneda'] == 'CLP')
            total_usd = sum(Decimal(item['monto']) for item in items if item['moneda'] == 'USD')
            totales[tipo] = {'clp': str(total_clp), 'usd': str(total_usd)}
        
        return Response({
            'year': year,
            'mes': mes,
            'registros': grouped,
            'totales': totales
        })

    @action(detail=False, methods=['post'])
    def bulk_update(self, request):
        """Bulk update multiple records"""
        updates = request.data.get('updates', [])
        
        for update in updates:
            try:
                registro = RegistroMensual.objects.get(id=update['id'], user=request.user)
                if 'monto' in update:
                    registro.monto = update['monto']
                if 'notas' in update:
                    registro.notas = update.get('notas', '')
                registro.save()
            except RegistroMensual.DoesNotExist:
                continue
        
        return Response({'status': 'updated', 'count': len(updates)})


class GastoAnualViewSet(viewsets.ModelViewSet):
    """API for annual expenses"""
    serializer_class = GastoAnualSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['nombre']
    ordering_fields = ['mes_cobro', 'nombre']
    ordering = ['mes_cobro']

    def get_queryset(self):
        return GastoAnual.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=['get'])
    def total_ahorro_mensual(self, request):
        """Calculate total monthly savings needed for annual expenses"""
        total = sum(
            g.ahorro_mensual for g in GastoAnual.objects.filter(user=request.user, activo=True)
        )
        return Response({'total_ahorro_mensual_clp': str(total)})


class GastoTrimestralViewSet(viewsets.ModelViewSet):
    """API for quarterly expenses"""
    serializer_class = GastoTrimestralSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['nombre']
    ordering_fields = ['trimestre', 'nombre']
    ordering = ['trimestre']

    def get_queryset(self):
        return GastoTrimestral.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=['get'])
    def total_ahorro_mensual(self, request):
        """Calculate total monthly savings needed for quarterly expenses"""
        total = sum(
            g.ahorro_mensual for g in GastoTrimestral.objects.filter(user=request.user, activo=True)
        )
        return Response({'total_ahorro_mensual_clp': str(total)})
