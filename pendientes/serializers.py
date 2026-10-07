from rest_framework import serializers

from .models import OcurrenciaPendiente, ReglaPendiente


class ReglaPendienteSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReglaPendiente
        fields = (
            "id", "nombre", "descripcion", "tipo", "departamento", "producto",
            "periodicidad", "fecha_inicio", "dia_vencimiento", "meses_vencimiento",
            "monto_estimado", "moneda", "url_pago", "canal_cobro", "activa",
        )
        read_only_fields = ("id",)

    def validate(self, attrs):
        user = self.context["request"].user
        for field in ("departamento", "producto"):
            obj = attrs.get(field)
            if obj is not None and obj.user_id != user.id:
                raise serializers.ValidationError({field: "El recurso no pertenece al usuario."})
        return attrs


class OcurrenciaPendienteSerializer(serializers.ModelSerializer):
    regla_nombre = serializers.CharField(source="regla.nombre", read_only=True)
    departamento = serializers.IntegerField(source="regla.departamento_id", read_only=True)

    class Meta:
        model = OcurrenciaPendiente
        fields = ("id", "regla", "regla_nombre", "departamento", "fecha_vencimiento", "monto_esperado", "estado", "fecha_real", "monto_real")
        read_only_fields = ("id", "regla", "regla_nombre", "departamento", "fecha_vencimiento")
