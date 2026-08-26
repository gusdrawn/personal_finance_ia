"""Extracción e ingesta de estados de cuenta PDF.

La primera versión es deliberadamente explícita: reconoce los formatos de Itaú,
Banco Falabella y Santander que sirven como prueba de concepto. El parser trabaja
con texto extraído, no con credenciales bancarias ni conexiones externas, y deja
el resultado asociado al archivo original para poder revisar qué se importó.
"""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from bank_sync.models import MovimientoNormalizado
from banca.models import ImportacionEstadoCuenta, SaldoSnapshot
from banca.services.ingesta import ingestar_movimientos
from configuracion.models import Banco, Producto


class EstadoCuentaParseError(ValueError):
    """El archivo no corresponde a un formato soportado o no tiene texto legible."""


@dataclass
class EstadoCuentaParseado:
    banco: str
    tarjeta_ultimos4: str
    fecha_estado: date | None
    periodo_inicio: date | None
    periodo_fin: date | None
    monto_facturado: Decimal | None
    monto_minimo: Decimal | None
    movimientos: list[MovimientoNormalizado]


_NUMERO = r"-?[\d.]+(?:,\d+)?"
_FECHA_LARGA = r"\d{2}/\d{2}/\d{4}"


def normalizar_monto(valor: str) -> Decimal:
    """Convierte montos CLP del formato chileno, incluyendo decimales extranjeros."""
    limpio = (valor or "").replace("$", "").replace(" ", "").replace("\xa0", "")
    if not limpio or limpio in {"-", "+"}:
        raise ValueError("Monto vacío")
    try:
        if "," in limpio:
            limpio = limpio.replace(".", "").replace(",", ".")
        else:
            limpio = limpio.replace(".", "")
        return Decimal(limpio)
    except InvalidOperation as exc:
        raise ValueError(f"Monto inválido: {valor!r}") from exc


def normalizar_texto(valor: str) -> str:
    valor = unicodedata.normalize("NFKD", valor or "")
    return "".join(c for c in valor if not unicodedata.combining(c)).lower()


def _fecha(valor: str, *, corta: bool = False) -> date:
    return datetime.strptime(valor, "%d/%m/%y" if corta else "%d/%m/%Y").date()


def _extraer_texto_pdf(contenido: bytes) -> str:
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(contenido)) as pdf:
            texto = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as exc:  # pragma: no cover - el detalle depende de la librería PDF
        raise EstadoCuentaParseError("No se pudo leer el PDF.") from exc
    if not texto.strip():
        raise EstadoCuentaParseError(
            "El PDF no contiene texto extraíble; esta POC todavía no procesa escaneos."
        )
    return texto


def _detectar_banco(texto: str) -> str:
    normalizado = normalizar_texto(texto)
    if "bancofalabella" in normalizado or "cmr puntos" in normalizado:
        return "Banco Falabella"
    if "itau" in normalizado or "banco itau" in normalizado:
        return "Itaú"
    if "santander" in normalizado or "worldmember" in normalizado:
        return "Santander"
    raise EstadoCuentaParseError(
        "Banco no reconocido. La POC soporta Itaú, Banco Falabella y Santander."
    )


def _detectar_tarjeta(texto: str) -> str:
    patrones = [
        r"N[º°]\s+DE\s+TARJETA(?:\s+DE\s+CR[EÉ]DITO)?\s*:?\s*([0-9Xx* -]{8,})",
        r"N[º°]\s+DE\s+CONTRATO\s*:\s*([0-9Xx* -]{8,})",
        r"TITULAR\s*:\s*([0-9Xx* -]{8,})",
    ]
    for patron in patrones:
        match = re.search(patron, texto, re.IGNORECASE)
        if match:
            digitos = re.findall(r"\d", match.group(1))
            if digitos:
                return "".join(digitos)[-4:]
    return ""


def _detectar_fecha_estado(texto: str) -> date | None:
    patrones = [
        rf"FECHA(?:\s+DE)?\s+ESTADO\s+DE\s+CUENTA\s*:?\s*({_FECHA_LARGA})",
        rf"FECHA\s+FACTURACI[ÓO]N\s+ESTADO\s+DE\s+CUENTA\s*:\s*({_FECHA_LARGA})",
    ]
    for patron in patrones:
        match = re.search(patron, texto, re.IGNORECASE)
        if match:
            return _fecha(match.group(1))
    return None


def _detectar_periodo(texto: str) -> tuple[date | None, date | None]:
    match = re.search(
        rf"PER[IÍ]ODO\s+FACTURAD[OÓ]\s+({_FECHA_LARGA})\s+({_FECHA_LARGA})",
        texto,
        re.IGNORECASE,
    )
    if not match:
        return None, None
    return _fecha(match.group(1)), _fecha(match.group(2))


def _detectar_monto(texto: str, etiqueta: str) -> Decimal | None:
    # No permitimos que el espacio del regex atraviese una línea y capture el día
    # de una fecha del cupón de pago en vez del monto real.
    match = re.search(rf"{etiqueta}[ \t]*\$?[ \t]*({_NUMERO})", texto, re.IGNORECASE)
    return normalizar_monto(match.group(1)) if match else None


def _operacion(
    *,
    fecha: date,
    descripcion: str,
    monto: Decimal,
    tarjeta: str,
    periodo: date | None,
    cuota_actual: int | None = None,
    cuota_total: int | None = None,
) -> MovimientoNormalizado:
    descripcion = " ".join((descripcion or "").split()).strip(" -")[:255]
    return MovimientoNormalizado(
        fecha=fecha,
        descripcion=descripcion or "Operación sin descripción",
        monto=monto,
        moneda="CLP",
        estado="FACTURADO",
        tarjeta_id=tarjeta or None,
        cuota_actual=cuota_actual,
        cuota_total=cuota_total,
        periodo_factura=periodo,
        tipo_banco="estado_cuenta_pdf",
    )


def _parsear_itau(texto: str, tarjeta: str, periodo: date | None) -> list[MovimientoNormalizado]:
    movimientos = []
    patron = re.compile(
        rf"^\s*({_FECHA_LARGA})\s+\d{{10,}}\s+(.+?)\s+\$\s*({_NUMERO})\s+\$\s*{_NUMERO}\s+(\d{{1,2}})/(\d{{1,2}})\s+\$\s*{_NUMERO}\s*$",
        re.IGNORECASE,
    )
    for linea in texto.splitlines():
        match = patron.match(linea)
        if not match:
            continue
        movimientos.append(
            _operacion(
                fecha=_fecha(match.group(1)),
                descripcion=match.group(2),
                monto=normalizar_monto(match.group(3)),
                tarjeta=tarjeta,
                periodo=periodo,
                cuota_actual=int(match.group(4)),
                cuota_total=int(match.group(5)),
            )
        )
    return movimientos


def _parsear_falabella(
    texto: str, tarjeta: str, periodo: date | None
) -> list[MovimientoNormalizado]:
    movimientos = []
    patron_numero = re.compile(
        rf"(.+?)\s+T\s+({_NUMERO})\s+{_NUMERO}\s+(\d{{1,2}})/(\d{{1,2}})\b",
        re.IGNORECASE,
    )
    for linea in texto.splitlines():
        fecha_match = re.search(_FECHA_LARGA, linea)
        if not fecha_match:
            continue
        resto = linea[fecha_match.end() :].strip()
        match = patron_numero.search(resto)
        if not match:
            continue
        movimientos.append(
            _operacion(
                fecha=_fecha(fecha_match.group(0)),
                descripcion=match.group(1),
                monto=normalizar_monto(match.group(2)),
                tarjeta=tarjeta,
                periodo=periodo,
                cuota_actual=int(match.group(3)),
                cuota_total=int(match.group(4)),
            )
        )
    return movimientos


def _parsear_santander(
    texto: str, tarjeta: str, periodo: date | None
) -> list[MovimientoNormalizado]:
    movimientos = []
    patron_monto = re.compile(rf"\$\s*({_NUMERO})\s*$")
    for linea in texto.splitlines():
        # El comprobante de pago también contiene fechas y montos, pero no es una
        # operación de la tarjeta.
        if "BANCO $" in linea.upper() or "COMPROBANTE DE PAGO" in linea.upper():
            continue
        fecha_match = re.search(r"\d{2}/\d{2}/\d{2}", linea)
        if not fecha_match:
            continue
        monto_match = patron_monto.search(linea)
        if not monto_match:
            continue
        descripcion = linea[fecha_match.end() : monto_match.start()]
        # En compras extranjeras Santander imprime el monto original antes del CLP.
        descripcion = re.sub(rf"\s*/?\s*A\s+VE\s+{_NUMERO}\s*$", "", descripcion, flags=re.I)
        descripcion = re.sub(rf"\s+{_NUMERO}\s*$", "", descripcion)
        movimientos.append(
            _operacion(
                fecha=_fecha(fecha_match.group(0), corta=True),
                descripcion=descripcion,
                monto=normalizar_monto(monto_match.group(1)),
                tarjeta=tarjeta,
                periodo=periodo,
            )
        )
    return movimientos


def parsear_estado_cuenta_texto(texto: str, nombre_archivo: str = "") -> EstadoCuentaParseado:
    """Parsea texto extraído; es la entrada determinista usada por los tests."""
    banco = _detectar_banco(texto)
    tarjeta = _detectar_tarjeta(texto)
    fecha_estado = _detectar_fecha_estado(texto)
    periodo_inicio, periodo_fin = _detectar_periodo(texto)
    periodo = fecha_estado or periodo_fin
    if banco == "Itaú":
        movimientos = _parsear_itau(texto, tarjeta, periodo)
    elif banco == "Banco Falabella":
        movimientos = _parsear_falabella(texto, tarjeta, periodo)
    else:
        movimientos = _parsear_santander(texto, tarjeta, periodo)
    return EstadoCuentaParseado(
        banco=banco,
        tarjeta_ultimos4=tarjeta,
        fecha_estado=fecha_estado,
        periodo_inicio=periodo_inicio,
        periodo_fin=periodo_fin,
        monto_facturado=_detectar_monto(texto, r"MONTO\s+TOTAL\s+FACTURADO\s+A\s+PAGAR"),
        monto_minimo=_detectar_monto(texto, r"MONTO\s+M[IÍ]NIMO\s+A\s+PAGAR"),
        movimientos=movimientos,
    )


def parsear_estado_cuenta_pdf(contenido: bytes, nombre_archivo: str = "") -> EstadoCuentaParseado:
    return parsear_estado_cuenta_texto(_extraer_texto_pdf(contenido), nombre_archivo)


def _leer_archivo(archivo) -> bytes:
    if isinstance(archivo, bytes):
        return archivo
    contenido = archivo.read()
    try:
        archivo.seek(0)
    except (AttributeError, OSError):
        pass
    return contenido


def _banco_existente(nombre: str) -> Banco:
    objetivo = normalizar_texto(nombre)
    for banco in Banco.objects.all():
        actual = normalizar_texto(banco.nombre)
        if actual == objetivo or objetivo in actual or actual in objetivo:
            return banco
    return Banco.objects.create(nombre=nombre, activo=True)


def _producto_tarjeta(banco: Banco, ultimos4: str) -> Producto:
    if ultimos4:
        existente = Producto.objects.filter(
            banco=banco, tipo="TDC", nombre__icontains=ultimos4
        ).first()
        if existente:
            return existente
    nombre = f"TDC {banco.nombre}" + (f" ••••{ultimos4}" if ultimos4 else " (PDF)")
    producto, _ = Producto.objects.get_or_create(
        banco=banco,
        nombre=nombre,
        defaults={"tipo": "TDC", "activo": True},
    )
    return producto


def _guardar_snapshot(user, producto: Producto, resultado: EstadoCuentaParseado) -> None:
    if not resultado.fecha_estado or resultado.monto_facturado is None:
        return
    SaldoSnapshot.objects.update_or_create(
        user=user,
        producto=producto,
        fecha=resultado.fecha_estado,
        defaults={"deuda_facturada_clp": resultado.monto_facturado},
    )


@transaction.atomic
def importar_estado_cuenta(user, archivo, *, nombre_archivo: str | None = None) -> ImportacionEstadoCuenta:
    """Procesa un archivo; los errores quedan registrados y no abortan el lote."""
    contenido = _leer_archivo(archivo)
    nombre = nombre_archivo or getattr(archivo, "name", "estado_cuenta.pdf")
    huella = hashlib.sha256(contenido).hexdigest()
    existente = ImportacionEstadoCuenta.objects.filter(user=user, hash_archivo=huella).first()
    if existente:
        # Conservamos el registro original como PROCESADO; la huella evita crear
        # una segunda importación y la ingesta sigue siendo idempotente.
        existente.fue_duplicado = True
        return existente

    registro = ImportacionEstadoCuenta(
        user=user,
        nombre_archivo=nombre[:255],
        hash_archivo=huella,
        estado="ERROR",
    )
    registro.fue_duplicado = False
    registro.archivo.save(nombre[:255], ContentFile(contenido), save=False)
    try:
        resultado = parsear_estado_cuenta_pdf(contenido, nombre)
        banco = _banco_existente(resultado.banco)
        producto = _producto_tarjeta(banco, resultado.tarjeta_ultimos4)
        ingesta = ingestar_movimientos(user, banco, resultado.movimientos)
        registro.banco = banco
        registro.producto = producto
        registro.estado = "PROCESADO"
        registro.banco_detectado = resultado.banco
        registro.tarjeta_ultimos4 = resultado.tarjeta_ultimos4
        registro.fecha_estado = resultado.fecha_estado
        registro.periodo_inicio = resultado.periodo_inicio
        registro.periodo_fin = resultado.periodo_fin
        registro.monto_facturado = resultado.monto_facturado
        registro.monto_minimo = resultado.monto_minimo
        registro.operaciones_detectadas = len(resultado.movimientos)
        registro.operaciones_nuevas = ingesta["nuevos"]
        registro.operaciones_actualizadas = ingesta["actualizados"]
        registro.mensaje = "Análisis completado."
        registro.procesado_at = timezone.now()
        registro.save()
        registro.movimientos.add(*ingesta["ids"])
        _guardar_snapshot(user, producto, resultado)
    except Exception as exc:
        registro.mensaje = str(exc)[:1000]
        registro.save()
    return registro
