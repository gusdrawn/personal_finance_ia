from django.db import models


class Banco(models.Model):
    """Banks and financial institutions"""
    nombre = models.CharField(max_length=100)
    logo = models.ImageField(upload_to='bancos/', null=True, blank=True)
    orden = models.IntegerField(default=0)
    activo = models.BooleanField(default=True)
    notas = models.TextField(blank=True, help_text="Datos de transferencia e información relevante")
    mostrar_en_carga_masiva = models.BooleanField(
        default=True, 
        help_text="Mostrar inputs de esta entidad en la carga masiva (ej. comisiones)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Banco'
        verbose_name_plural = 'Bancos'
        ordering = ['orden', 'nombre']

    def __str__(self):
        return self.nombre


class Producto(models.Model):
    """Bank products (credit cards, lines of credit, etc)"""
    PRODUCTO_TIPOS = [
        ('TDC', 'Tarjeta de Crédito'),
        ('LINEA_CREDITO', 'Línea de Crédito'),
        ('CREDITO_CONSUMO', 'Crédito de Consumo'),
        ('CREDITO_HIPOTECARIO', 'Crédito Hipotecario'),
        ('CUENTA', 'Cuenta Corriente'),
        ('COBRO_BANCO', 'Cobro de Banco'),
        ('OTRO', 'Otro'),
    ]

    banco = models.ForeignKey(Banco, on_delete=models.CASCADE, related_name='productos')
    nombre = models.CharField(max_length=150)
    tipo = models.CharField(max_length=20, choices=PRODUCTO_TIPOS)
    orden = models.IntegerField(default=0)
    activo = models.BooleanField(default=True)
    tiene_cupo_usd = models.BooleanField(
        default=False,
        help_text="Si es Tarjeta de Crédito, indica si maneja un cupo en dólares separado"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Producto'
        verbose_name_plural = 'Productos'
        ordering = ['banco__orden', 'orden', 'nombre']

    def __str__(self):
        return f"{self.banco.nombre} - {self.nombre}"


class TipoCambio(models.Model):
    """Daily UF and USD exchange rates"""
    fecha = models.DateField(db_index=True)
    uf = models.DecimalField(max_digits=10, decimal_places=4, help_text="Valor de la UF en CLP")
    dolar = models.DecimalField(max_digits=10, decimal_places=4, help_text="Valor del USD en CLP")
    fuente = models.CharField(
        max_length=50,
        default='mindicador.cl',
        help_text="Source of the data (mindicador.cl, manual, etc)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Tipo de Cambio'
        verbose_name_plural = 'Tipos de Cambio'
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['-fecha']),
        ]
        unique_together = ('fecha', 'fuente')

    def __str__(self):
        return f"{self.fecha}: UF ${self.uf} / USD ${self.dolar}"


class ConfiguracionGeneral(models.Model):
    """General system configuration per user"""
    user = models.OneToOneField('auth.User', on_delete=models.CASCADE, related_name='configuracion_general')
    banco_defecto_salario = models.ForeignKey(
        Banco,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+'
    )
    dia_recordatorio_alimentacion = models.IntegerField(
        default=1,
        help_text="Day of month for data entry reminder (1-28)"
    )
    mostrar_modo_lectura = models.BooleanField(
        default=False,
        help_text="Show read-only mode by default"
    )
    actualizar_tc_automatico = models.BooleanField(
        default=True,
        help_text="Auto-update exchange rates daily"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Configuración General'
        verbose_name_plural = 'Configuraciones Generales'

    def __str__(self):
        return f"Configuration for {self.user.username}"

