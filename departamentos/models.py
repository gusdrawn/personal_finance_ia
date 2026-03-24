from django.db import models
from decimal import Decimal
from django.db.models import Q
from datetime import date


class Departamento(models.Model):
    """Rental apartment properties"""
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='departamentos')
    codigo = models.CharField(max_length=20)  # E107, E205, etc
    piso = models.CharField(max_length=20)
    metros_cuadrados = models.DecimalField(max_digits=8, decimal_places=2)
    valor_compra_uf = models.DecimalField(max_digits=10, decimal_places=2, help_text="Purchase price in UF")
    valor_actual_uf = models.DecimalField(max_digits=10, decimal_places=2, help_text="Current market value in UF")
    avaluo_fiscal = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    tiene_estacionamientos = models.BooleanField(default=False)
    numero_bodegas = models.IntegerField(default=0)
    activo = models.BooleanField(default=True)
    notas = models.TextField(blank=True)
    fecha_inicio = models.DateField(null=True, blank=True, help_text="Fecha de inicio del pago")
    fecha_ultima_cuota = models.DateField(null=True, blank=True, help_text="Fecha de la última cuota")
    plazo_anos = models.IntegerField(null=True, blank=True, help_text="Plazo en años (opcional si hay fecha_ultima_cuota)")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Departamento'
        verbose_name_plural = 'Departamentos'
        ordering = ['codigo']
        unique_together = ('user', 'codigo')

    def __str__(self):
        return f"{self.codigo} - {self.piso}"

    @property
    def ganancia_capital_uf(self):
        """Unrealized capital gain in UF"""
        return self.valor_actual_uf - self.valor_compra_uf

    @property
    def porcentaje_ganancia_capital(self):
        """Percentage capital gain"""
        if self.valor_compra_uf == 0:
            return Decimal('0')
        return (self.ganancia_capital_uf / self.valor_compra_uf) * 100

    @property
    def progreso_pago(self):
        """Calculates payment progress percentage based on dates"""
        if not self.fecha_inicio:
            return 0.0
            
        today = date.today()
        if today < self.fecha_inicio:
            return 0.0
            
        if self.fecha_ultima_cuota:
            fin = self.fecha_ultima_cuota
        elif self.plazo_anos:
            # Approximate end date
            try:
                fin = self.fecha_inicio.replace(year=self.fecha_inicio.year + self.plazo_anos)
            except ValueError:
                # Handle leap year Feb 29
                fin = self.fecha_inicio.replace(year=self.fecha_inicio.year + self.plazo_anos, day=28)
        else:
            return 0.0

        if today >= fin:
            return 100.0

        total_days = (fin - self.fecha_inicio).days
        passed_days = (today - self.fecha_inicio).days
        
        if total_days <= 0:
            return 100.0
            
        return round((passed_days / total_days) * 100.0, 2)


class Arrendatario(models.Model):
    """Tenant/renter information"""
    departamento = models.OneToOneField(Departamento, on_delete=models.CASCADE, related_name='arrendatario')
    nombre = models.CharField(max_length=150)
    rut = models.CharField(max_length=20, unique=True)
    fecha_inicio_contrato = models.DateField()
    monto_arriendo_clp = models.DecimalField(max_digits=15, decimal_places=2)
    monto_arriendo_usd = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    telefono = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    activo = models.BooleanField(default=True)
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Arrendatario'
        verbose_name_plural = 'Arrendatarios'

    def __str__(self):
        return f"{self.nombre} - {self.departamento.codigo}"


class CreditoHipotecario(models.Model):
    """Mortgage credit for apartment"""
    TIPO_TASA = [
        ('FIJA', 'Tasa Fija'),
        ('SEMIVARIABLE', 'Tasa Semivariable'),
        ('VARIABLE', 'Tasa Variable'),
    ]

    departamento = models.OneToOneField(Departamento, on_delete=models.CASCADE, related_name='credito_hipotecario')
    banco = models.ForeignKey('configuracion.Banco', on_delete=models.PROTECT)
    monto_original_uf = models.DecimalField(max_digits=10, decimal_places=2)
    tasa_anual = models.DecimalField(max_digits=5, decimal_places=3, help_text="Annual interest rate %")
    plazo_anos = models.IntegerField(help_text="Loan term in years")
    tipo_tasa = models.CharField(max_length=15, choices=TIPO_TASA)
    dfl2 = models.BooleanField(default=False, help_text="DFL2 tax benefit")
    cuota_uf = models.DecimalField(max_digits=10, decimal_places=4, help_text="Monthly payment in UF")
    saldo_actual_uf = models.DecimalField(max_digits=10, decimal_places=2, help_text="Current outstanding balance in UF")
    cuotas_pagadas = models.IntegerField(default=0)
    cuotas_totales = models.IntegerField()
    cae = models.CharField(max_length=20, blank=True)
    activo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Crédito Hipotecario'
        verbose_name_plural = 'Créditos Hipotecarios'

    def __str__(self):
        return f"Hipoteca {self.departamento.codigo} - {self.banco.nombre}"

    @property
    def cuotas_restantes(self):
        return self.cuotas_totales - self.cuotas_pagadas

    def calcular_amortizacion_mensual(self):
        """Calculate monthly principal amortization using French amortization formula"""
        tasa_mensual = self.tasa_anual / 12 / 100
        interes_mes_uf = self.saldo_actual_uf * tasa_mensual
        amortizacion_mes_uf = self.cuota_uf - interes_mes_uf
        return max(Decimal('0'), amortizacion_mes_uf)

    def proyectar_amortizacion(self, meses=12):
        """Project amortization schedule for N months"""
        saldo = self.saldo_actual_uf
        tasa_mensual = self.tasa_anual / 12 / 100
        proyeccion = []

        for i in range(1, meses + 1):
            interes = saldo * tasa_mensual
            amortizacion = self.cuota_uf - interes
            saldo = saldo - amortizacion
            
            proyeccion.append({
                'mes': i,
                'cuota_uf': self.cuota_uf,
                'interes_uf': interes,
                'amortizacion_uf': max(Decimal('0'), amortizacion),
                'saldo_uf': max(Decimal('0'), saldo),
            })

        return proyeccion


class Estacionamiento(models.Model):
    """Parking spaces assigned to tenants"""
    departamento = models.ForeignKey(Departamento, on_delete=models.CASCADE, related_name='estacionamientos')
    numero = models.IntegerField()
    asignado_a_arrendatario = models.BooleanField(default=True)
    arrendatario = models.ForeignKey(
        Arrendatario,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='estacionamientos'
    )
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Estacionamiento'
        verbose_name_plural = 'Estacionamientos'
        unique_together = ('departamento', 'numero')

    def __str__(self):
        return f"Est. {self.numero} - {self.departamento.codigo}"
