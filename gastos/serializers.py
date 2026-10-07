from datetime import date

from rest_framework import serializers

from banca.models import CategoriaTematica
from gastos.models import GastoProgramado, MovimientoManual
from gastos.services import convertir_a_clp, primer_dia


class CategoriaTematicaSerializer(serializers.ModelSerializer):
    class Meta:
        model = CategoriaTematica
        fields = [
            "id", "nombre", "aplica_a", "origen", "esencialidad_defecto",
            "es_interes_o_costo_financiero", "activa", "orden",
        ]
        read_only_fields = ["id", "origen"]


class MovimientoManualSerializer(serializers.ModelSerializer):
    categoria_nombre = serializers.CharField(source="categoria.nombre", read_only=True)

    class Meta:
        model = MovimientoManual
        fields = [
            "id", "fecha", "periodo", "direccion", "monto", "moneda", "monto_clp",
            "tipo_cambio", "monto_clp_estimado", "categoria", "categoria_nombre",
            "departamento", "naturaleza", "descripcion", "notas", "es_resumen_mensual", "modo_resumen",
        ]
        read_only_fields = [
            "id", "monto_clp", "tipo_cambio", "monto_clp_estimado", "categoria_nombre",
        ]

    def validate_categoria(self, categoria):
        if categoria.user_id != self.context["request"].user.id:
            raise serializers.ValidationError("La categoría no pertenece al usuario.")
        return categoria

    def validate_departamento(self, departamento):
        if departamento and departamento.user_id != self.context["request"].user.id:
            raise serializers.ValidationError("La propiedad no pertenece al usuario.")
        return departamento

    def validate(self, attrs):
        fecha = attrs.get("fecha") or getattr(self.instance, "fecha", None) or date.today()
        periodo = attrs.get("periodo") or getattr(self.instance, "periodo", None) or primer_dia(fecha)
        attrs["periodo"] = primer_dia(periodo)
        categoria = attrs.get("categoria") or getattr(self.instance, "categoria", None)
        direccion = attrs.get("direccion") or getattr(self.instance, "direccion", None)
        if categoria and categoria.aplica_a not in {direccion, "AMBOS"}:
            raise serializers.ValidationError({"categoria": "La categoría no admite esa dirección."})
        monto = attrs.get("monto", getattr(self.instance, "monto", None))
        moneda = attrs.get("moneda", getattr(self.instance, "moneda", "CLP"))
        if monto is not None and (
            self.instance is None or any(campo in attrs for campo in ("monto", "moneda", "fecha"))
        ):
            monto_clp, tasa, estimado = convertir_a_clp(monto, moneda, fecha)
            if monto_clp <= 0:
                raise serializers.ValidationError({"monto": "No existe un tipo de cambio para convertir el monto."})
            attrs.update(monto_clp=monto_clp, tipo_cambio=tasa, monto_clp_estimado=estimado)
        return attrs


class GastoProgramadoSerializer(serializers.ModelSerializer):
    ahorro_mensual = serializers.DecimalField(max_digits=15, decimal_places=2, read_only=True)

    class Meta:
        model = GastoProgramado
        fields = ["id", "nombre", "monto", "fecha_inicio", "frecuencia", "activo", "ahorro_mensual", "notas"]
        read_only_fields = ["id", "ahorro_mensual"]
