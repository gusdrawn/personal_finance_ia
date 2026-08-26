"""
Modelos de la app `banca`.

Es el motor transaccional que reemplaza la carga manual de `gastos` (ver
docs/ARQUITECTURA_INTEGRACION_BANCARIA.md §16). El movimiento individual es la fuente de
verdad; todo lo demás (presupuesto, conclusiones, pasivos del dashboard) se deriva.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
import secrets

from django.db import models

# --- choices compartidos ------------------------------------------------------------
MONEDA_CHOICES = [("CLP", "Peso chileno"), ("USD", "Dólar")]
ESENCIALIDAD_CHOICES = [("FIJO", "Fijo"), ("OCIO", "Ocio")]
NATURALEZA_CHOICES = [
    ("OPERACIONAL", "Operacional"),
    ("TRANSFERENCIA", "Transferencia / no operacional"),
]
ESTADO_CHOICES = [
    ("NO_FACTURADO", "No facturado"),
    ("FACTURADO", "Facturado"),
    ("PAGADO", "Pagado"),
]
FUENTE_CHOICES = [
    ("PENDIENTE", "Sin clasificar"),
    ("IA", "IA"),
    ("REGLA", "Regla aprendida"),
    ("MANUAL", "Manual"),
]


TIPO_FUENTE_CHOICES = [
    ("banco", "Banco"),
    ("cripto", "Cripto / Exchange"),
    ("inversion", "Inversiones"),
    ("afp", "Previsión (AFP / AFC)"),
]


class CredencialBanco(models.Model):
    """
    Credencial cifrada (Fernet) de una fuente de datos. Ningún secreto en texto plano.

    Cubre dos familias de fuentes (ver docs/ARQUITECTURA_INTEGRACION_BANCARIA.md):
      - Bancos: se autentican con RUT + clave (`rut` / `clave_cifrada`).
      - Activos (cripto/inversión/AFP): se autentican con API key/secret
        (`api_key_cifrada` / `api_secret_cifrada`) o una dirección pública
        (`identificador_publico`, p.ej. una address o xpub de wallet, sin secreto).
    """

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="credenciales_banco"
    )
    banco = models.ForeignKey(
        "configuracion.Banco", on_delete=models.CASCADE, related_name="credenciales"
    )
    conector = models.CharField(
        max_length=30,
        default="santander",
        help_text="Nombre del connector en bank_sync",
    )
    tipo_fuente = models.CharField(
        max_length=15, choices=TIPO_FUENTE_CHOICES, default="banco"
    )
    # --- autenticación de bancos ---
    rut = models.CharField(max_length=20, blank=True, default="")
    clave_cifrada = models.BinaryField(null=True, blank=True)
    # --- autenticación de fuentes de activos (API key / dirección pública) ---
    api_key_cifrada = models.BinaryField(null=True, blank=True)
    api_secret_cifrada = models.BinaryField(null=True, blank=True)
    identificador_publico = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Dirección/xpub pública para wallets de solo lectura (sin secreto).",
    )
    activo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Credencial de Fuente"
        verbose_name_plural = "Credenciales de Fuente"
        unique_together = ("user", "banco")

    def __str__(self):
        return (
            f"{self.banco} · {self.rut or self.identificador_publico or self.conector}"
        )

    # helpers de cifrado
    def set_clave(self, clave: str):
        from banca.crypto import encrypt

        self.clave_cifrada = encrypt(clave)

    def get_clave(self) -> str:
        from banca.crypto import decrypt

        return decrypt(self.clave_cifrada)

    def set_api_key(self, valor: str):
        from banca.crypto import encrypt

        self.api_key_cifrada = encrypt(valor)

    def get_api_key(self) -> str:
        from banca.crypto import decrypt

        return decrypt(self.api_key_cifrada)

    def set_api_secret(self, valor: str):
        from banca.crypto import encrypt

        self.api_secret_cifrada = encrypt(valor)

    def get_api_secret(self) -> str:
        from banca.crypto import decrypt

        return decrypt(self.api_secret_cifrada)


class CategoriaTematica(models.Model):
    """Categoría granular para análisis (suscripción, impuesto, supermercado, etc.)."""

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="categorias_tematicas"
    )
    nombre = models.CharField(max_length=100)
    esencialidad_defecto = models.CharField(
        max_length=4, choices=ESENCIALIDAD_CHOICES, default="OCIO"
    )
    es_interes_o_costo_financiero = models.BooleanField(
        default=False,
        help_text="Marca categorías de interés/comisión para conclusiones",
    )
    activa = models.BooleanField(default=True)
    orden = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Categoría Temática"
        verbose_name_plural = "Categorías Temáticas"
        ordering = ["orden", "nombre"]
        unique_together = ("user", "nombre")

    def __str__(self):
        return f"{self.nombre} ({self.get_esencialidad_defecto_display()})"


class MapeoComercio(models.Model):
    """comercio (glosa normalizada) -> categoría aprendida. Evita re-llamar a la IA."""

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="mapeos_comercio"
    )
    patron = models.CharField(
        max_length=255, db_index=True, help_text="Glosa normalizada (substring)"
    )
    categoria = models.ForeignKey(
        CategoriaTematica, on_delete=models.CASCADE, related_name="mapeos"
    )
    origen = models.CharField(
        max_length=10, choices=[("IA", "IA"), ("MANUAL", "Manual")], default="IA"
    )
    hits = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Mapeo de Comercio"
        verbose_name_plural = "Mapeos de Comercio"
        ordering = ["-hits", "patron"]
        unique_together = ("user", "patron")

    def __str__(self):
        return f"{self.patron} → {self.categoria.nombre}"


class ContraparteConocida(models.Model):
    """RUT/alias de personas con las que haces bicicleta; sus transfers no son ingreso."""

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="contrapartes"
    )
    alias = models.CharField(
        max_length=120, help_text="Nombre o glosa que aparece en la transferencia"
    )
    rut = models.CharField(max_length=20, blank=True)
    prestamo = models.ForeignKey(
        "prestamos.Prestamo",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="contrapartes_banca",
    )
    activo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Contraparte Conocida"
        verbose_name_plural = "Contrapartes Conocidas"
        ordering = ["alias"]

    def __str__(self):
        return self.alias


class MovimientoBancario(models.Model):
    """Transacción individual: la fuente de verdad. Una fila por movimiento real."""

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="movimientos_banca"
    )
    producto = models.ForeignKey(
        "configuracion.Producto",
        on_delete=models.PROTECT,
        related_name="movimientos_banca",
    )

    fecha = models.DateField(db_index=True, help_text="Fecha de compra (devengo)")
    descripcion = models.CharField(max_length=255)
    monto = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        help_text="En moneda original (+ cargo / - abono)",
    )
    moneda = models.CharField(max_length=3, choices=MONEDA_CHOICES, default="CLP")
    monto_clp = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    tipo_cambio_banco = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True
    )

    cuota_actual = models.IntegerField(null=True, blank=True)
    cuota_total = models.IntegerField(null=True, blank=True)

    estado = models.CharField(
        max_length=15, choices=ESTADO_CHOICES, default="NO_FACTURADO", db_index=True
    )
    periodo_factura = models.DateField(
        null=True, blank=True, help_text="A qué estado de cuenta pertenece"
    )

    naturaleza = models.CharField(
        max_length=15, choices=NATURALEZA_CHOICES, default="OPERACIONAL", db_index=True
    )
    categoria = models.ForeignKey(
        CategoriaTematica,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="movimientos",
    )
    esencialidad = models.CharField(
        max_length=4, choices=ESENCIALIDAD_CHOICES, default="OCIO"
    )
    fuente_clasificacion = models.CharField(
        max_length=10, choices=FUENTE_CHOICES, default="PENDIENTE"
    )
    confianza_ia = models.DecimalField(
        max_digits=4, decimal_places=3, null=True, blank=True
    )
    categoria_es_manual = models.BooleanField(default=False)
    clasificado_at = models.DateTimeField(null=True, blank=True)

    # bicicleta financiera
    prestamo = models.ForeignKey(
        "prestamos.Prestamo",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="movimientos_banca",
    )
    por_revisar = models.BooleanField(
        default=False,
        help_text="Abono ambiguo: confirmar si es ingreso o transferencia",
    )
    naturaleza_es_manual = models.BooleanField(
        default=False,
        help_text="El usuario fijó naturaleza/préstamo a mano; el sync no lo pisa",
    )

    hash_externo = models.CharField(max_length=64)
    importaciones = models.ManyToManyField(
        "ImportacionEstadoCuenta",
        blank=True,
        related_name="movimientos",
        help_text="Estados de cuenta PDF desde los que se detectó este movimiento.",
    )
    notificado_telegram = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Movimiento Bancario"
        verbose_name_plural = "Movimientos Bancarios"
        ordering = ["-fecha", "-id"]
        indexes = [
            models.Index(fields=["user", "estado", "fecha"]),
            models.Index(fields=["user", "naturaleza", "fecha"]),
            models.Index(fields=["categoria", "fecha"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "hash_externo"],
                name="banca_movimiento_hash_user_unico",
            )
        ]

    def __str__(self):
        return f"{self.fecha} · {self.descripcion} · {self.monto} {self.moneda}"

    # --- semántica de gasto/ingreso ---
    @property
    def es_cargo(self) -> bool:
        return self.monto_clp >= 0

    @property
    def contabiliza(self) -> bool:
        """Solo lo OPERACIONAL entra a gasto/ingreso/presupuesto."""
        return self.naturaleza == "OPERACIONAL" and not self.por_revisar

    @property
    def es_gasto_ocio(self) -> bool:
        return self.contabiliza and self.es_cargo and self.esencialidad == "OCIO"


class ImportacionEstadoCuenta(models.Model):
    """Archivo de estado de cuenta analizado y su resultado auditable."""

    ESTADO_CHOICES = [
        ("PROCESADO", "Procesado"),
        ("DUPLICADO", "Duplicado"),
        ("ERROR", "Error"),
    ]

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="importaciones_estado_cuenta"
    )
    archivo = models.FileField(upload_to="estados_cuenta/%Y/%m/")
    nombre_archivo = models.CharField(max_length=255)
    hash_archivo = models.CharField(max_length=64)
    estado = models.CharField(max_length=12, choices=ESTADO_CHOICES, default="PROCESADO")
    banco_detectado = models.CharField(max_length=100, blank=True)
    banco = models.ForeignKey(
        "configuracion.Banco",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="importaciones_estado_cuenta",
    )
    producto = models.ForeignKey(
        "configuracion.Producto",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="importaciones_estado_cuenta",
    )
    tarjeta_ultimos4 = models.CharField(max_length=4, blank=True)
    fecha_estado = models.DateField(null=True, blank=True)
    periodo_inicio = models.DateField(null=True, blank=True)
    periodo_fin = models.DateField(null=True, blank=True)
    monto_facturado = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    monto_minimo = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    operaciones_detectadas = models.PositiveIntegerField(default=0)
    operaciones_nuevas = models.PositiveIntegerField(default=0)
    operaciones_actualizadas = models.PositiveIntegerField(default=0)
    mensaje = models.TextField(blank=True)
    procesado_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Importación de Estado de Cuenta"
        verbose_name_plural = "Importaciones de Estados de Cuenta"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "hash_archivo"],
                name="banca_importacion_estado_hash_user_unico",
            )
        ]

    def __str__(self):
        return f"{self.nombre_archivo} · {self.banco_detectado or 'No detectado'}"


class SaldoSnapshot(models.Model):
    """Foto de deuda/cupos de un producto en una fecha (alimenta pasivos del patrimonio)."""

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="saldos_banca"
    )
    producto = models.ForeignKey(
        "configuracion.Producto", on_delete=models.CASCADE, related_name="saldos_banca"
    )
    fecha = models.DateField(db_index=True)

    deuda_facturada_clp = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    deuda_no_facturada_clp = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    cupo_total_clp = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    cupo_disponible_clp = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )

    cupo_usd = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    cupo_usd_disponible = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    deuda_usd = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    tipo_cambio_banco = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True
    )

    # crédito / hipotecario
    saldo_insoluto = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    saldo_insoluto_uf = models.DecimalField(
        max_digits=15,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="Saldo en UF (hipotecarios); el CLP se deriva con el TC",
    )
    proxima_cuota = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    proxima_cuota_uf = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True
    )
    fecha_proximo_pago = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Snapshot de Saldo"
        verbose_name_plural = "Snapshots de Saldo"
        ordering = ["-fecha"]
        unique_together = ("producto", "fecha")

    def __str__(self):
        return f"{self.producto} @ {self.fecha}"

    @property
    def deuda_total_clp(self) -> Decimal:
        return (
            (self.deuda_facturada_clp or Decimal("0"))
            + (self.deuda_no_facturada_clp or Decimal("0"))
            + (self.saldo_insoluto or Decimal("0"))
        )


class SyncLog(models.Model):
    """Auditoría de cada corrida de sincronización."""

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="sync_logs"
    )
    banco = models.ForeignKey(
        "configuracion.Banco", on_delete=models.CASCADE, related_name="sync_logs"
    )
    MODO = [
        ("no_facturado", "No facturados"),
        ("estado_cuenta", "Estado de cuenta"),
        ("deudas", "Deudas"),
        ("full", "Completa"),
    ]
    modo = models.CharField(max_length=20, choices=MODO)
    iniciado = models.DateTimeField(auto_now_add=True)
    finalizado = models.DateTimeField(null=True, blank=True)
    exito = models.BooleanField(default=False)
    nuevos = models.IntegerField(default=0)
    actualizados = models.IntegerField(default=0)
    clasificados_ia = models.IntegerField(default=0)
    mensaje = models.TextField(blank=True)

    class Meta:
        verbose_name = "Log de Sincronización"
        verbose_name_plural = "Logs de Sincronización"
        ordering = ["-iniciado"]

    def __str__(self):
        estado = "OK" if self.exito else "ERROR"
        return f"{self.banco} · {self.modo} · {self.iniciado:%Y-%m-%d %H:%M} · {estado}"


class ConfiguracionBanca(models.Model):
    """Parámetros del presupuesto y la sincronización, por usuario."""

    user = models.OneToOneField(
        "auth.User", on_delete=models.CASCADE, related_name="config_banca"
    )
    ventana_meses_ingreso = models.IntegerField(
        default=3, help_text="Meses para promediar ingresos"
    )
    umbral_alerta_ocio = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=Decimal("0.80"),
        help_text="Fracción del disponible Ocio que dispara el aviso (0.80 = 80%)",
    )
    tolerancia_comision_bicicleta = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        default=Decimal("0.030"),
        help_text="Tolerancia de comisión para calzar cargo NeatPagos a un préstamo (0.03 = 3%)",
    )
    # --- proveedor de IA (compatible con OpenAI: OpenRouter, OpenCode Zen, etc.) ---
    ia_base_url = models.CharField(
        max_length=200,
        default="https://openrouter.ai/api/v1",
        help_text="Base URL del proveedor compatible OpenAI (ej. OpenCode Zen). Sin /chat/completions.",
    )
    ia_api_key_cifrada = models.BinaryField(null=True, blank=True)
    modelo_ia = models.CharField(
        max_length=120,
        default="anthropic/claude-3.5-haiku",
        help_text="Modelo seleccionado para clasificar",
    )
    modelos_disponibles = models.JSONField(
        default=list,
        blank=True,
        help_text="Cache de IDs de modelo traídos del endpoint /models del proveedor",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Configuración de Banca"
        verbose_name_plural = "Configuraciones de Banca"

    def __str__(self):
        return f"Config banca de {self.user}"

    # helpers de cifrado de la API key de IA (mismo Fernet que las credenciales)
    def set_ia_api_key(self, clave: str):
        from banca.crypto import encrypt

        self.ia_api_key_cifrada = encrypt(clave) if clave else None

    def get_ia_api_key(self) -> str:
        if not self.ia_api_key_cifrada:
            return ""
        from banca.crypto import decrypt

        return decrypt(self.ia_api_key_cifrada)


class ApplePayApiKey(models.Model):
    """Token de acceso para el Atajo de Apple Pay (POC).

    El token en texto plano solo se devuelve al crearlo. En la base de datos se conserva
    únicamente su hash SHA-256, por lo que una filtración de esta tabla no permite copiar
    directamente el secreto del iPhone.
    """

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="apple_pay_api_keys"
    )
    nombre = models.CharField(max_length=80, default="Atajo iPhone")
    prefijo = models.CharField(
        max_length=16,
        help_text="Prefijo no secreto para identificar la llave sin mostrarla completa.",
    )
    clave_hash = models.CharField(max_length=64, unique=True, db_index=True)
    activo = models.BooleanField(default=True)
    ultimo_uso_at = models.DateTimeField(null=True, blank=True)
    revocado_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "API key de Apple Pay"
        verbose_name_plural = "API keys de Apple Pay"
        ordering = ["-created_at"]

    def __str__(self):
        estado = "activa" if self.activo and not self.revocado_at else "revocada"
        return f"{self.nombre} · {self.prefijo}… ({estado})"

    @staticmethod
    def hash_clave(clave: str) -> str:
        return hashlib.sha256(clave.encode("utf-8")).hexdigest()

    @classmethod
    def crear(cls, user, *, nombre: str = "Atajo iPhone") -> tuple["ApplePayApiKey", str]:
        """Crea una llave y devuelve `(registro, secreto)` una sola vez."""
        secreto = f"fpa_apple_{secrets.token_urlsafe(32)}"
        registro = cls.objects.create(
            user=user,
            nombre=(nombre or "Atajo iPhone").strip()[:80] or "Atajo iPhone",
            prefijo=secreto[:16],
            clave_hash=cls.hash_clave(secreto),
        )
        return registro, secreto


class ApplePayEvent(models.Model):
    """Auditoría e idempotencia de eventos recibidos desde Apple Shortcuts."""

    ESTADO_CHOICES = [
        ("PROCESADO", "Procesado"),
        ("DUPLICADO", "Duplicado"),
        ("ERROR", "Error"),
    ]

    user = models.ForeignKey(
        "auth.User", on_delete=models.CASCADE, related_name="apple_pay_events"
    )
    api_key = models.ForeignKey(
        ApplePayApiKey,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="eventos",
    )
    event_id = models.CharField(max_length=120)
    payload_hash = models.CharField(max_length=64)
    movimiento = models.ForeignKey(
        MovimientoBancario,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="apple_pay_eventos",
    )
    estado = models.CharField(max_length=12, choices=ESTADO_CHOICES, default="PROCESADO")
    respuesta = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Evento Apple Pay"
        verbose_name_plural = "Eventos Apple Pay"
        ordering = ["-received_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "event_id"],
                name="banca_apple_pay_event_user_id_unico",
            )
        ]

    def __str__(self):
        return f"{self.event_id} · {self.estado}"
