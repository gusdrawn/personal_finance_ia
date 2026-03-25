from rest_framework import serializers
from .models import CategoriaIngreso, RegistroMensual, GastoProgramado


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


class GastoProgramadoSerializer(serializers.ModelSerializer):
    ahorro_mensual = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)
    
    class Meta:
        model = GastoProgramado
        fields = ['id', 'nombre', 'monto', 'fecha_inicio', 'frecuencia', 'activo', 'ahorro_mensual', 'notas']
        read_only_fields = ['id', 'ahorro_mensual']
