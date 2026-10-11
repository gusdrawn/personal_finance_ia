from django.db import models, transaction
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from decimal import Decimal, ROUND_HALF_UP


def normalizar_monto(monto):
    """Round entered currency amounts to the two decimal places we store."""
    return Decimal(str(monto or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


MONEDA_ORIGINAL_CHOICES = [("CLP", "Peso chileno"), ("USD", "Dólar")]


def _saldo_sin_equivalente_clp(saldo):
    monto = getattr(saldo, 'saldo_uf', None)
    if monto is None and saldo.moneda_original == 'USD':
        monto = saldo.monto_original
    return bool(monto and not saldo.monto_clp)


class Activo(models.Model):
    """Unified asset model: cash, investments, properties, loans given, etc."""
    HORIZONTE_CHOICES = [
        ('EFECTIVO', 'Efectivo'),
        ('CORTO_PLAZO', 'Corto Plazo'),
        ('MEDIANO_PLAZO', 'Mediano Plazo'),
        ('LARGO_PLAZO', 'Largo Plazo'),
    ]

    TIPO_ACTIVO = [
        ('EFECTIVO', 'Efectivo'),
        ('CRIPTO', 'Criptomoneda'),
        ('FONDO_MUTUO', 'Fondo Mutuo'),
        ('ACCIONES', 'Acciones'),
        ('BROKERAGE', 'Brokerage Account'),
        ('PLAZO_FIJO', 'Plazo Fijo'),
        ('PRESTAMO_DADO', 'Préstamo Dado'),
        ('CUENTA_COBRAR', 'Cuenta por cobrar'),
        ('DEPARTAMENTO', 'Departamento'),
        ('AHORRO', 'Ahorro'),
        ('OTRO', 'Otro'),
    ]

    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='activos')
    nombre = models.CharField(max_length=150)
    tipo = models.CharField(max_length=20, choices=TIPO_ACTIVO)
    horizonte_temporal = models.CharField(
        max_length=20, choices=HORIZONTE_CHOICES, default='EFECTIVO',
        help_text="Horizonte de inversión del activo"
    )
    es_liquido = models.BooleanField(default=True, help_text="¿Se puede liquidar fácilmente?")
    # monto_clp/monto_usd se conservan como valores de compatibilidad y caché.
    # El saldo canónico tiene una única moneda de origen.
    monto_clp = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    monto_usd = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    monto_original = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    moneda_original = models.CharField(
        max_length=3, choices=MONEDA_ORIGINAL_CHOICES, null=True, blank=True
    )
    requiere_revision_moneda = models.BooleanField(default=False)
    instrumento = models.ForeignKey(
        "trading.Instrument",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="activos_patrimoniales",
    )
    fuente_externa = models.CharField(max_length=40, blank=True)
    cuenta_fuente = models.CharField(max_length=120, blank=True)
    id_fuente = models.CharField(max_length=180, blank=True)
    requiere_revision_fuente = models.BooleanField(default=False)
    activo = models.BooleanField(default=True)
    notas = models.TextField(blank=True)
    departamento = models.OneToOneField(
        'departamentos.Departamento',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='activo_patrimonial'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Activo'
        verbose_name_plural = 'Activos'
        ordering = ['horizonte_temporal', 'tipo', 'nombre']
        constraints = [
            models.UniqueConstraint(
                fields=["user", "fuente_externa", "cuenta_fuente", "id_fuente"],
                condition=~models.Q(id_fuente=""),
                name="patrimonio_activo_fuente_identidad_unica",
            )
        ]

    def clean(self):
        if self.departamento_id and self.departamento.user_id != self.user_id:
            raise ValidationError({"departamento": "La propiedad debe pertenecer al usuario."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def asignar_saldo_original(self, monto, moneda, tipo_cambio=None):
        """Set the source balance and refresh available reporting-currency caches."""
        valor = normalizar_monto(monto)
        if moneda not in {"CLP", "USD"}:
            raise ValidationError("La moneda del saldo debe ser CLP o USD.")
        self.monto_original = valor
        self.moneda_original = moneda
        self.requiere_revision_moneda = False
        if moneda == "CLP":
            self.monto_clp = valor
            if tipo_cambio and tipo_cambio.dolar:
                self.monto_usd = (valor / tipo_cambio.dolar).quantize(Decimal("0.01"))
        else:
            self.monto_usd = valor
            if tipo_cambio and tipo_cambio.dolar:
                self.monto_clp = (valor * tipo_cambio.dolar).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.nombre} - CLP ${self.monto_clp} / USD ${self.monto_usd}"

    @property
    def conversion_clp_desconocida(self):
        return _saldo_sin_equivalente_clp(self)


class CuentaPorCobrar(models.Model):
    """Agrupador de importes pagados por el usuario que aún debe recuperar."""

    MONEDA_CHOICES = [
        ("CLP", "Peso chileno"),
        ("USD", "Dólar"),
    ]

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="cuentas_por_cobrar"
    )
    nombre = models.CharField(max_length=150)
    descripcion = models.TextField(blank=True)
    moneda = models.CharField(max_length=3, choices=MONEDA_CHOICES, default="CLP")
    activo = models.BooleanField(default=True)
    activo_patrimonial = models.OneToOneField(
        Activo,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cuenta_por_cobrar",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Cuenta por cobrar"
        verbose_name_plural = "Cuentas por cobrar"
        ordering = ["-activo", "nombre"]

    def __str__(self):
        return self.nombre

    @property
    def total_cargado(self):
        return sum((detalle.monto for detalle in self.detalles.all() if detalle.vigente), Decimal("0"))

    @property
    def total_abonado(self):
        return sum((abono.monto for abono in self.abonos.all() if abono.vigente), Decimal("0"))

    @property
    def saldo_pendiente(self):
        confirmado = self.calculos.filter(estado="CONFIRMADO").order_by("-periodo", "-revision").first()
        if confirmado is not None:
            return confirmado.saldo_final
        legado = self.calculos.filter(estado="REFERENCIA", referencia_usuario=False).first()
        if legado is not None:
            return legado.saldo_final
        return max(self.total_cargado - self.total_abonado, Decimal("0"))

    def sincronizar_activo(self):
        """Refleja el saldo pendiente en el activo unificado del patrimonio."""
        saldo = self.saldo_pendiente
        activo = (
            Activo.objects.filter(pk=self.activo_patrimonial_id).first()
            if self.activo_patrimonial_id
            else None
        )
        if activo is not None and activo.user_id != self.user_id:
            activo = None
        if activo is None:
            activo = Activo.objects.create(
                user=self.user,
                nombre=self.nombre,
                tipo="CUENTA_COBRAR",
                horizonte_temporal="CORTO_PLAZO",
                es_liquido=False,
                activo=self.activo,
            )
            type(self).objects.filter(pk=self.pk).update(activo_patrimonial=activo)
            self.activo_patrimonial_id = activo.pk

        activo.nombre = self.nombre
        activo.tipo = "CUENTA_COBRAR"
        activo.horizonte_temporal = "CORTO_PLAZO"
        activo.es_liquido = False
        activo.activo = self.activo
        activo.monto_clp = saldo if self.moneda == "CLP" else Decimal("0")
        activo.monto_usd = saldo if self.moneda == "USD" else Decimal("0")
        activo.monto_original = saldo
        activo.moneda_original = self.moneda
        activo.requiere_revision_moneda = False
        activo.notas = self.descripcion
        activo.save(
            update_fields=[
                "nombre",
                "tipo",
                "horizonte_temporal",
                "es_liquido",
                "activo",
                "monto_clp",
                "monto_usd",
                "monto_original",
                "moneda_original",
                "requiere_revision_moneda",
                "notas",
                "updated_at",
            ]
        )


class DetalleCuentaPorCobrar(models.Model):
    """Una obligación o cargo que compone una cuenta por cobrar."""

    ORIGEN_CHOICES = [
        ("MANUAL", "Manual"),
        ("MOVIMIENTO", "Tarjeta de crédito"),
        ("NEAT", "NeatPagos"),
    ]

    cuenta = models.ForeignKey(
        CuentaPorCobrar, on_delete=models.CASCADE, related_name="detalles"
    )
    descripcion = models.CharField(max_length=255)
    periodo = models.DateField(db_index=True, help_text="Mes al que corresponde el cobro")
    fecha = models.DateField(help_text="Fecha del cargo o registro manual")
    monto = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    origen = models.CharField(max_length=12, choices=ORIGEN_CHOICES, default="MANUAL")
    monto_manual = models.BooleanField(
        default=True,
        help_text="Permite distinguir una cifra ingresada a mano de una detectada.",
    )
    provisional = models.BooleanField(
        default=False,
        help_text="El cargo ya ocurrió, pero su fuente bancaria aún no fue importada.",
    )
    vigente = models.BooleanField(default=True, help_text="Conserva las líneas retiradas para auditoría.")
    movimiento = models.ForeignKey(
        "banca.MovimientoBancario",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="detalles_cuentas_por_cobrar",
    )
    evento_externo = models.ForeignKey(
        "banca.EventoFinancieroExterno",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="detalles_cuentas_por_cobrar",
    )
    clave_similitud = models.CharField(max_length=255, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Detalle de cuenta por cobrar"
        verbose_name_plural = "Detalles de cuentas por cobrar"
        ordering = ["-periodo", "-fecha", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=~(
                    models.Q(movimiento__isnull=False)
                    & models.Q(evento_externo__isnull=False)
                ),
                name="patrimonio_cobro_unica_fuente",
            ),
            models.UniqueConstraint(
                fields=["cuenta", "movimiento"],
                condition=models.Q(movimiento__isnull=False),
                name="patrimonio_cobro_movimiento_unico",
            ),
            models.UniqueConstraint(
                fields=["cuenta", "evento_externo"],
                condition=models.Q(evento_externo__isnull=False),
                name="patrimonio_cobro_evento_unico",
            ),
        ]

    def clean(self):
        if self.movimiento_id and self.movimiento.user_id != self.cuenta.user_id:
            raise ValidationError("El movimiento debe pertenecer al mismo usuario de la cuenta.")
        if self.evento_externo_id and self.evento_externo.user_id != self.cuenta.user_id:
            raise ValidationError("El evento NeatPagos debe pertenecer al mismo usuario de la cuenta.")
        fuente = self.movimiento if self.movimiento_id else self.evento_externo if self.evento_externo_id else None
        if fuente is not None:
            from patrimonio.services.cuentas_por_cobrar import validar_fuente_no_solapada

            try:
                validar_fuente_no_solapada(fuente, self.cuenta, self.pk)
            except ValueError as error:
                raise ValidationError(str(error)) from error

    @transaction.atomic
    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.cuenta.nombre} · {self.descripcion} · {self.monto}"

    @property
    def total_abonado(self):
        return sum((abono.monto for abono in self.abonos.all() if abono.vigente), Decimal("0"))

    @property
    def saldo_pendiente(self):
        return max(self.monto - self.total_abonado, Decimal("0"))


class AbonoCuentaPorCobrar(models.Model):
    """Pago recibido, asociado opcionalmente a una línea y a su movimiento bancario."""

    cuenta = models.ForeignKey(
        CuentaPorCobrar, on_delete=models.CASCADE, related_name="abonos"
    )
    detalle = models.ForeignKey(
        DetalleCuentaPorCobrar,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="abonos",
    )
    movimiento = models.ForeignKey(
        "banca.MovimientoBancario",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="abonos_cuentas_por_cobrar",
    )
    fecha = models.DateField(db_index=True)
    periodo = models.DateField(null=True, blank=True, db_index=True,
                               help_text="Mes de aplicación del abono; puede diferir de su fecha bancaria.")
    monto = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    descripcion = models.CharField(max_length=255, blank=True)
    vigente = models.BooleanField(default=True, help_text="Un abono retirado o conciliado no se cuenta otra vez.")
    conciliado_con = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="registros_conciliados",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Abono de cuenta por cobrar"
        verbose_name_plural = "Abonos de cuentas por cobrar"
        ordering = ["-fecha", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["movimiento"],
                condition=models.Q(movimiento__isnull=False),
                name="patrimonio_abono_movimiento_unico",
            )
        ]

    def clean(self):
        if self.conciliado_con_id and self.conciliado_con.cuenta_id != self.cuenta_id:
            raise ValidationError("Los abonos conciliados deben pertenecer a esta cuenta.")
        if self.detalle_id and self.detalle.cuenta_id != self.cuenta_id:
            raise ValidationError("El abono debe apuntar a una línea de su misma cuenta.")
        if self.movimiento_id and self.movimiento.user_id != self.cuenta.user_id:
            raise ValidationError("El movimiento debe pertenecer al mismo usuario de la cuenta.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.cuenta.nombre} · abono {self.monto}"


class CalculoCuentaPorCobrar(models.Model):
    """Versión mensual; el desglose y texto confirmados son una fotografía inmutable."""

    ESTADOS = [("REFERENCIA", "Referencia sin validar"), ("BORRADOR", "Borrador"), ("CONFIRMADO", "Confirmado")]
    cuenta = models.ForeignKey(CuentaPorCobrar, on_delete=models.CASCADE, related_name="calculos")
    periodo = models.DateField(db_index=True)
    revision = models.PositiveIntegerField(default=1)
    estado = models.CharField(max_length=12, choices=ESTADOS, default="BORRADOR")
    referencia_usuario = models.BooleanField(default=False)
    anterior = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="posteriores")
    fecha_calculo = models.DateField()
    saldo_anterior = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    cargos = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    abonos = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    ajustes = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    saldo_final = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    desglose = models.JSONField(default=dict)
    texto = models.TextField(blank=True)
    huella = models.CharField(max_length=64, blank=True)
    confirmado_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-periodo", "-revision"]
        constraints = [
            models.UniqueConstraint(fields=["cuenta", "periodo", "revision"], name="cobro_calculo_revision_unica"),
            models.UniqueConstraint(fields=["cuenta", "periodo"], condition=models.Q(estado="BORRADOR"), name="cobro_borrador_mes_unico"),
        ]

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, estado__in=["CONFIRMADO", "REFERENCIA"]).exists():
            raise ValidationError("Los cálculos históricos son inmutables; registra un ajuste en el mes abierto.")
        if self.anterior_id and self.anterior.cuenta_id != self.cuenta_id:
            raise ValidationError("El cálculo anterior debe pertenecer a esta cuenta.")
        return super().save(*args, **kwargs)


class PartidaRevisionCobro(models.Model):
    """Decisión pendiente; sus fuentes no afectan la contabilidad hasta confirmar."""

    TIPOS = [("CARGO", "Cargo"), ("ABONO", "Abono"), ("EDITAR", "Editar cargo"), ("RETIRAR_CARGO", "Retirar cargo"), ("RETIRAR_ABONO", "Retirar abono"), ("AJUSTE", "Ajuste")]
    DECISIONES = [("PENDIENTE", "Pendiente"), ("APLICAR", "Aplicar"), ("RESPALDO", "Ya incluido · respaldo"), ("CONCILIAR", "Conciliar con abono existente"), ("EXCLUIR", "Excluir de esta cuenta")]
    cuenta = models.ForeignKey(CuentaPorCobrar, on_delete=models.CASCADE, related_name="partidas_revision")
    periodo = models.DateField(db_index=True)
    periodo_mensual = models.DateField(null=True, blank=True,
        help_text="Mes de la reconstrucción provisional, independiente del mes de confirmación.")
    tipo = models.CharField(max_length=15, choices=TIPOS)
    clave = models.CharField(max_length=120)
    descripcion = models.CharField(max_length=255)
    fecha = models.DateField()
    monto = models.DecimalField(max_digits=15, decimal_places=2)
    movimiento = models.ForeignKey("banca.MovimientoBancario", null=True, blank=True, on_delete=models.PROTECT, related_name="revisiones_cobro")
    evento_externo = models.ForeignKey("banca.EventoFinancieroExterno", null=True, blank=True, on_delete=models.PROTECT, related_name="revisiones_cobro")
    detalle = models.ForeignKey(DetalleCuentaPorCobrar, null=True, blank=True, on_delete=models.PROTECT, related_name="revisiones")
    abono = models.ForeignKey(AbonoCuentaPorCobrar, null=True, blank=True, on_delete=models.PROTECT, related_name="revisiones")
    conciliar_con = models.ForeignKey(AbonoCuentaPorCobrar, null=True, blank=True, on_delete=models.PROTECT, related_name="propuestas_conciliacion")
    decision = models.CharField(max_length=10, choices=DECISIONES, default="PENDIENTE")
    motivo = models.CharField(max_length=255, blank=True)
    provisional = models.BooleanField(default=False)
    registrada_por_usuario = models.BooleanField(default=False,
        help_text="Incluye el registro guardado explícitamente en la vista mensual provisional.")
    resuelta_en = models.ForeignKey(CalculoCuentaPorCobrar, null=True, blank=True, on_delete=models.PROTECT, related_name="partidas_resueltas")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["fecha", "id"]
        constraints = [models.UniqueConstraint(fields=["cuenta", "clave"], condition=models.Q(resuelta_en__isnull=True), name="cobro_partida_pendiente_unica")]

    @property
    def periodo_vista(self):
        return self.periodo_mensual or self.periodo

    def clean(self):
        if self.resuelta_en_id and self.resuelta_en.cuenta_id != self.cuenta_id:
            raise ValidationError("La confirmación debe pertenecer a esta cuenta.")
        for fuente in [self.movimiento, self.evento_externo]:
            if fuente and fuente.user_id != self.cuenta.user_id:
                raise ValidationError("La fuente debe pertenecer al usuario de la cuenta.")
            if fuente and fuente.moneda != self.cuenta.moneda:
                raise ValidationError("La moneda de la fuente no coincide con la cuenta.")
        for registro in [self.detalle, self.abono, self.conciliar_con]:
            if registro and registro.cuenta_id != self.cuenta_id:
                raise ValidationError("Los registros deben pertenecer a esta cuenta.")
        if self.movimiento_id and self.evento_externo_id:
            raise ValidationError("Una partida sólo puede tener una fuente financiera.")
        if self.tipo != "AJUSTE" and self.monto <= 0:
            raise ValidationError("El importe debe ser positivo.")
        if self.tipo == "AJUSTE" and (not self.motivo or self.monto == 0):
            raise ValidationError("El ajuste requiere un importe distinto de cero y un motivo.")

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk, resuelta_en__isnull=False).exists():
            raise ValidationError("La partida ya fue confirmada.")
        self.full_clean()
        return super().save(*args, **kwargs)


class HistorialActivo(models.Model):
    """Historical snapshots of asset values"""
    activo = models.ForeignKey(Activo, on_delete=models.CASCADE, related_name='historial')
    fecha = models.DateField(db_index=True)
    monto_clp = models.DecimalField(max_digits=15, decimal_places=2)
    monto_usd = models.DecimalField(max_digits=15, decimal_places=2)
    valor_uf = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    valor_dolar = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Historial Activo'
        verbose_name_plural = 'Historiales Activos'
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['activo', '-fecha']),
        ]
        unique_together = ('activo', 'fecha')

    def __str__(self):
        return f"{self.activo.nombre} - {self.fecha}"


class Pasivo(models.Model):
    """Liabilities (credit cards, mortgages, loans, etc)"""
    TIPO_PASIVO = [
        ('TDC', 'Tarjeta de Crédito'),
        ('CREDITO_HIPOTECARIO', 'Crédito Hipotecario'),
        ('CREDITO_CONSUMO', 'Crédito de Consumo'),
        ('PRESTAMO', 'Préstamo'),
        ('OTRO', 'Otro'),
    ]

    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='pasivos')
    nombre = models.CharField(max_length=150)
    tipo = models.CharField(max_length=25, choices=TIPO_PASIVO)
    monto_clp = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    monto_usd = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    monto_original = models.DecimalField(max_digits=15, decimal_places=2, null=True, blank=True)
    moneda_original = models.CharField(
        max_length=3, choices=MONEDA_ORIGINAL_CHOICES, null=True, blank=True
    )
    requiere_revision_moneda = models.BooleanField(default=False)
    sincronizado_banca = models.BooleanField(default=False)
    producto = models.ForeignKey(
        'configuracion.Producto',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='pasivos'
    )
    activo = models.BooleanField(default=True, help_text="Si la deuda sigue vigente")
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Pasivo'
        verbose_name_plural = 'Pasivos'
        ordering = ['tipo', 'nombre']
        constraints = [
            models.UniqueConstraint(
                fields=["producto", "moneda_original"],
                condition=models.Q(sincronizado_banca=True, producto__isnull=False),
                name="patrimonio_pasivo_banca_producto_moneda_unico",
            )
        ]

    def clean(self):
        if self.producto_id and self.producto.user_id != self.user_id:
            raise ValidationError({"producto": "El producto debe pertenecer al usuario."})

    @property
    def conversion_clp_desconocida(self):
        return _saldo_sin_equivalente_clp(self)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def asignar_saldo_original(self, monto, moneda, tipo_cambio=None):
        """Set the source balance and refresh available reporting-currency caches."""
        valor = normalizar_monto(monto)
        if moneda not in {"CLP", "USD"}:
            raise ValidationError("La moneda del saldo debe ser CLP o USD.")
        self.monto_original = valor
        self.moneda_original = moneda
        self.requiere_revision_moneda = False
        if moneda == "CLP":
            self.monto_clp = valor
            if tipo_cambio and tipo_cambio.dolar:
                self.monto_usd = (valor / tipo_cambio.dolar).quantize(Decimal("0.01"))
        else:
            self.monto_usd = valor
            if tipo_cambio and tipo_cambio.dolar:
                self.monto_clp = (valor * tipo_cambio.dolar).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.nombre} - CLP ${self.monto_clp} / USD ${self.monto_usd}"


class SnapshotPatrimonio(models.Model):
    """Monthly snapshots of assets, liabilities, and net worth"""
    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='snapshots_patrimonio')
    fecha = models.DateField(db_index=True)
    activos_total_clp = models.DecimalField(max_digits=15, decimal_places=2)
    activos_total_usd = models.DecimalField(max_digits=15, decimal_places=2)
    pasivos_total_clp = models.DecimalField(max_digits=15, decimal_places=2)
    pasivos_total_usd = models.DecimalField(max_digits=15, decimal_places=2)
    patrimonio_neto_clp = models.DecimalField(max_digits=15, decimal_places=2)
    patrimonio_neto_usd = models.DecimalField(max_digits=15, decimal_places=2)
    activos_liquidos_clp = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    valor_dolar = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    valor_uf = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    notas = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Snapshot Patrimonio'
        verbose_name_plural = 'Snapshots Patrimonio'
        ordering = ['-fecha']
        indexes = [
            models.Index(fields=['user', '-fecha']),
        ]
        unique_together = ('user', 'fecha')

    def __str__(self):
        return f"{self.user.username} - {self.fecha}: Patrimonio ${self.patrimonio_neto_clp}"


class MiniSesion(models.Model):
    """Detailed notes for assets/liabilities with shareable feature"""
    TIPO_MINISESION = [
        ('PRESTAMO_DADO', 'Préstamo Dado'),
        ('COBRO_TERCERO', 'Cobro a Tercero'),
        ('DEUDA', 'Deuda'),
        ('OTRO', 'Otro'),
    ]

    user = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='minisesiones')
    nombre = models.CharField(max_length=150)
    fecha = models.DateField()
    tipo = models.CharField(max_length=20, choices=TIPO_MINISESION)
    activo_relacionado = models.ForeignKey(
        Activo,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='minisesiones'
    )
    compartible = models.BooleanField(
        default=False,
        help_text="Si es True, se pueden ocultar datos sensibles al compartir"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Mini Sesión'
        verbose_name_plural = 'Mini Sesiones'
        ordering = ['-fecha', 'nombre']

    def __str__(self):
        return f"{self.nombre} ({self.fecha})"

    @property
    def total(self):
        """Sum of all line items"""
        return sum(linea.monto for linea in self.lineas.all())


class LineaMiniSesion(models.Model):
    """Detail lines for mini-sessions"""
    sesion = models.ForeignKey(MiniSesion, on_delete=models.CASCADE, related_name='lineas')
    concepto = models.CharField(max_length=150)
    monto = models.DecimalField(max_digits=15, decimal_places=2)
    orden = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Línea Mini Sesión'
        verbose_name_plural = 'Líneas Mini Sesión'
        ordering = ['sesion', 'orden']

    def __str__(self):
        return f"{self.concepto}: ${self.monto}"
