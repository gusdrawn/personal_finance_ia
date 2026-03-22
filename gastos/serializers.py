from rest_framework import serializers
from .models import CategoriaIngreso, RegistroMensual, GastoAnual, GastoTrimestral


class CategoriaIngresoSerializer(serializers.ModelSerializer):
    class Meta:
        model = CategoriaIngreso
        fields = ['id', 'nombre', 'tipo', 'orden', 'activo', 'banco_defecto']
        read_only_fields = ['id']


class RegistroMensualSerializer(serializers.ModelSerializer):
    categoria_nombre = serializers.CharField(source='categoria.nombre', read_only=True)
    
    class Meta:
        model = RegistroMensual
        fields = ['id', 'year', 'mes', 'categoria', 'categoria_nombre', 'monto', 'moneda', 'tipo', 'notas']
        read_only_fields = ['id', 'categoria_nombre']


class GastoAnualSerializer(serializers.ModelSerializer):
    ahorro_mensual = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    
    class Meta:
        model = GastoAnual
        fields = ['id', 'nombre', 'monto', 'mes_cobro', 'activo', 'ahorro_mensual', 'notas']
        read_only_fields = ['id', 'ahorro_mensual']


class GastoTrimestralSerializer(serializers.ModelSerializer):
    ahorro_mensual = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    
    class Meta:
        model = GastoTrimestral
        fields = ['id', 'nombre', 'monto', 'trimestre', 'activo', 'ahorro_mensual', 'notas']
        read_only_fields = ['id', 'ahorro_mensual']
