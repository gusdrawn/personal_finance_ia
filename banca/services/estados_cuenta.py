"""Extracción e ingesta de estados de cuenta PDF.

La primera versión es deliberadamente explícita: reconoce los formatos de Itaú,
Banco Falabella, Santander, Scotiabank, BCI, Lider Bci y Tenpo que sirven como prueba de concepto. El parser trabaja
con texto extraído, no con credenciales bancarias ni conexiones externas, y deja
el resultado asociado al archivo original para poder revisar qué se importó.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.core.files.base import ContentFile
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from bank_sync.models import MovimientoNormalizado
from banca.models import ImportacionEstadoCuenta, MovimientoBancario, SaldoSnapshot
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
    moneda: str = "CLP"
    fecha_vencimiento: date | None = None
    cupo_total_clp: Decimal | None = None
    cupo_utilizado_clp: Decimal | None = None
    cupo_disponible_clp: Decimal | None = None
    cupo_avance_disponible_clp: Decimal | None = None
    cupo_total_usd: Decimal | None = None
    cupo_utilizado_usd: Decimal | None = None
    cupo_disponible_usd: Decimal | None = None
    tipo_producto: str = "TDC"
    numero_producto: str = ""
    saldo_final_clp: Decimal | None = None


@dataclass
class DocumentoAseoParseado:
    tipo_documento: str
    municipalidad: str
    comuna: str
    roles: list[str]
    fecha_emision: date | None = None
    fecha_vencimiento: date | None = None
    fecha_pago: date | None = None
    monto_base: Decimal | None = None
    monto_total: Decimal | None = None
    cuota_numero: int | None = None
    cuotas_totales: int | None = None
    referencia: str = ""


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


def _extraer_texto_ocr(contenido: bytes) -> str:
    """Extrae texto de PDFs escaneados cuando OCR está disponible."""
    import pytesseract
    import pypdfium2 as pdfium

    comando = os.environ.get("TESSERACT_CMD")
    if comando:
        pytesseract.pytesseract.tesseract_cmd = comando
    documento = pdfium.PdfDocument(contenido)
    paginas = []
    for pagina in documento:
        imagen = pagina.render(scale=2.5).to_pil()
        try:
            paginas.append(pytesseract.image_to_string(imagen, lang="spa+eng"))
        except Exception:
            paginas.append(pytesseract.image_to_string(imagen, lang="eng"))
    return "\n".join(paginas)


def _extraer_texto_pdf(contenido: bytes, *, intentar_ocr: bool = False) -> str:
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(contenido)) as pdf:
            texto = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as exc:  # pragma: no cover - el detalle depende de la librería PDF
        raise EstadoCuentaParseError("No se pudo leer el PDF.") from exc
    if texto.strip() and not intentar_ocr:
        return texto
    # Las órdenes municipales entregadas por algunos municipios son escaneos.
    # OCR es opcional para no romper instalaciones que solo importan cartolas
    # digitales; cuando está disponible se reutiliza para ambos tipos de PDF.
    try:
        texto_ocr = _extraer_texto_ocr(contenido)
        if texto_ocr.strip():
            return f"{texto}\n{texto_ocr}".strip() if texto.strip() else texto_ocr
    except Exception as exc:  # pragma: no cover - depende del binario local
        # Un PDF municipal puede conservar únicamente un encabezado o una firma
        # digital. En ese caso el nombre del archivo todavía puede permitir
        # identificarlo; no lo conviertas en un falso error de banco solo porque
        # Tesseract no está instalado.
        if texto.strip():
            return texto
        raise EstadoCuentaParseError(
            "El PDF no contiene texto extraíble. Para importar órdenes municipales escaneadas instala Tesseract OCR y configura TESSERACT_CMD."
        ) from exc
    if not texto.strip():
        raise EstadoCuentaParseError("No se pudo obtener texto del PDF, ni mediante OCR.")
    return texto


def _detectar_banco(texto: str) -> str:
    normalizado = normalizar_texto(texto)
    if "tenpo payments" in normalizado:
        return "Tenpo"
    if (
        "bancofalabella" in normalizado
        or "banco falabella" in normalizado
        or (
            "cuenta corriente / cartola historica" in normalizado
            and "sucursal btc" in normalizado
        )
    ):
        return "Banco Falabella"
    if "itau" in normalizado or "banco itau" in normalizado:
        return "Itaú"
    if (
        "santander" in normalizado
        or "s a n t a n d e r" in normalizado
        or "worldmember" in normalizado
    ):
        return "Santander"
    if (
        "estado de cuenta internacional de tarjeta de credito" in normalizado
        and "traspaso deuda nacional" in normalizado
    ):
        return "Santander"
    if "scotiabank" in normalizado or "scotia connect" in normalizado:
        return "Scotiabank"
    # BCI Personas se distingue de Lider Bci por su razón social y por el
    # encabezado de cuenta corriente. Se evalúa antes de Lider Bci porque ambos
    # comparten el token ``bci``.
    if (
        "banco credito inversiones" in normalizado
        or "bci personas" in normalizado
        or "cartola de cuenta corriente" in normalizado
    ):
        return "BCI"
    # El logotipo Lider Bci de algunos EECC es vectorial y no aparece en el texto
    # extraído. "SuperAvance" sí forma parte de la capa de texto y es distintivo
    # de este formato.
    if "cupo total superavance" in normalizado or "lider bci" in normalizado:
        return "Lider Bci"
    raise EstadoCuentaParseError(
        "Banco no reconocido. La POC soporta Itaú, Banco Falabella, Santander, Scotiabank, BCI, Lider Bci y Tenpo."
    )


def _detectar_tarjeta(texto: str) -> str:
    patrones = [
        r"[0-9]{4}[Xx*]{4,}[0-9]{4}\s+([0-9]{4}[Xx*]{4,}[0-9]{4})\s+\d{2}/\d{2}/\d{4}",
        r"N[º°]\s+TARJETA\s+DE\s+CR[EÉ]DITO\s+F[IÍ]SICA\s+([0-9Xx* -]{8,})",
        r"N[º°]\s+DE\s+TARJETA(?:\s+DE\s+CR[EÉ]DITO)?\s*:?\s*([0-9Xx* -]{8,})",
        r"N[ÚU]MERO\s+TARJETA\s*:?\s*([0-9Xx* -]{8,})",
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
        rf"FECHA\s+ESTADO\s+DE\s+CUENTA[^\n]*\n[^\n]*?({_FECHA_LARGA})\s*$",
        rf"FECHA(?:\s+DE)?\s+ESTADO\s+DE\s+CUENTA\s*:?\s*({_FECHA_LARGA})",
        rf"FECHA\s+FACTURACI[ÓO]N\s+ESTADO\s+DE\s+CUENTA\s*:\s*({_FECHA_LARGA})",
    ]
    for patron in patrones:
        match = re.search(patron, texto, re.IGNORECASE | re.MULTILINE)
        if match:
            return _fecha(match.group(1))
    return None


def _detectar_fecha_vencimiento(texto: str) -> date | None:
    """Detecta la fecha explícita de pago que los bancos rotulan como «Pagar hasta»."""
    etiqueta = re.compile(
        r"PAGAR\s+HASTA|FECHA(?:\s+DE)?\s+VENCIMIENTO",
        re.IGNORECASE,
    )
    lineas = texto.splitlines()
    for indice, linea in enumerate(lineas):
        match = etiqueta.search(linea)
        if not match:
            continue
        candidatos = [linea[match.end() :], *lineas[indice + 1 : indice + 3]]
        for candidato in candidatos:
            fecha = re.search(_FECHA_LARGA, candidato)
            if fecha:
                return _fecha(fecha.group(0))
    return None


def _detectar_periodo(texto: str) -> tuple[date | None, date | None]:
    match = re.search(
        rf"PER[IÍ]ODO\s+FACTURAD[OÓ]\s+({_FECHA_LARGA})\s+({_FECHA_LARGA})",
        texto,
        re.IGNORECASE,
    )
    if match:
        return _fecha(match.group(1)), _fecha(match.group(2))
    match = re.search(
        rf"PER[IÍ]ODO\s+FACTURADO\s+DESDE\s+({_FECHA_LARGA})"
        rf"[\s\S]{{0,160}}?PER[IÍ]ODO\s+FACTURADO\s+HASTA\s+({_FECHA_LARGA})",
        texto,
        re.IGNORECASE,
    )
    if match:
        return _fecha(match.group(1)), _fecha(match.group(2))
    return None, None


_MESES_ES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}


def _es_cartola_cuenta_corriente(texto: str, banco: str = "") -> bool:
    normalizado = normalizar_texto(texto)
    if "cuenta corriente" in normalizado and "cartola" in normalizado:
        return True
    if banco == "Scotiabank":
        return "consulta detalle de cartola emitida" in normalizado and "cargos" in normalizado and "abonos" in normalizado
    if banco == "BCI":
        return "cartola de cuenta corriente" in normalizado
    return False


def _detectar_numero_cuenta(texto: str, banco: str) -> str:
    patrones = {
        "Itaú": r"N[uú]mero\s+de\s+cuenta\s*:\s*([\d-]+)",
        "Banco Falabella": r"\bCuenta\s+([\d-]{8,})",
        "Santander": r"CUENTA\s+CORRIENTE\s+ML\s*\n[^\n]*\n\s*([\d-]{8,})",
        "Scotiabank": r"\bCuenta\s+([\d-]{8,})",
        "BCI": r"N[º°]\s*CUENTA\s*([\d-]{6,})",
    }
    match = re.search(patrones.get(banco, ""), texto, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _detectar_periodo_cuenta(texto: str, banco: str) -> tuple[date | None, date | None]:
    if banco == "Santander":
        match = re.search(
            rf"CARTOLA\s+DESDE\s+HASTA[\s\S]{{0,80}}?({_FECHA_LARGA})\s+({_FECHA_LARGA})",
            texto,
            re.IGNORECASE,
        )
        if match:
            return _fecha(match.group(1)), _fecha(match.group(2))
    if banco == "Itaú":
        match = re.search(
            r"Per[ií]odo\s*:\s*(\d{2})-([A-Za-zÁÉÍÓÚáéíóú]{3})-(\d{4})"
            r"\s*-\s*(\d{2})-([A-Za-zÁÉÍÓÚáéíóú]{3})-(\d{4})",
            texto,
            re.IGNORECASE,
        )
        if match:
            mes_inicio = _MESES_ES[normalizar_texto(match.group(2))[:3]]
            mes_fin = _MESES_ES[normalizar_texto(match.group(5))[:3]]
            return (
                date(int(match.group(3)), mes_inicio, int(match.group(1))),
                date(int(match.group(6)), mes_fin, int(match.group(4))),
            )
    if banco == "Banco Falabella":
        match = re.search(
            r"Tipo\s+de\s+([A-Za-zÁÉÍÓÚáéíóú]+)\s+(\d{4})", texto, re.IGNORECASE
        )
        if match:
            import calendar

            mes = _MESES_ES[normalizar_texto(match.group(1))[:3]]
            year = int(match.group(2))
            return date(year, mes, 1), date(year, mes, calendar.monthrange(year, mes)[1])
    if banco == "Scotiabank":
        match = re.search(
            rf"DESDE\s+({_FECHA_LARGA})[\s\S]{{0,100}}?HASTA\s+({_FECHA_LARGA})",
            texto,
            re.IGNORECASE,
        )
        if match:
            return _fecha(match.group(1)), _fecha(match.group(2))
    if banco == "BCI":
        match = re.search(
            r"PERIODO\s+(\d{2})-(\d{2})-(\d{4})\s+al\s+(\d{2})-(\d{2})-(\d{4})",
            texto,
            re.IGNORECASE,
        )
        if match:
            return (
                date(int(match.group(3)), int(match.group(2)), int(match.group(1))),
                date(int(match.group(6)), int(match.group(5)), int(match.group(4))),
            )
    return None, None


def _detectar_saldo_final_cuenta(texto: str, banco: str) -> Decimal | None:
    if banco == "Itaú":
        match = re.search(
            r"Saldo\s+final\s+Saldo\s+disponible[\s\S]{0,120}?\$\s*([\d.-]+)",
            texto, re.I,
        )
    elif banco == "Banco Falabella":
        match = re.search(
            r"Saldo\s+Inicial\s+Saldo\s+Contable[\s\S]{0,100}?"
            r"\$\s*[\d.-]+\s+\$\s*([\d.-]+)",
            texto, re.I,
        )
    elif banco == "Scotiabank":
        # El formato de consulta no rotula un saldo final adicional: la última
        # fila de movimientos termina en la columna ``Saldo``.
        filas = re.findall(
            rf"^\s*{_FECHA_LARGA}\s+.+?\s+\d{{9}}\s+(?:-?[\d.]+\s+)?(-?[\d.]+)\s*$",
            texto,
            re.I | re.M,
        )
        return normalizar_monto(filas[-1]) if filas else None
    elif banco == "BCI":
        match = re.search(
            r"SALDO\s+CONTABLE\s+FINAL\s+DEL\s+PERIODO[\s\S]{0,120}?=\s*(-?[\d.]+)",
            texto,
            re.I,
        )
        return normalizar_monto(match.group(1)) if match else None
    else:
        matches = re.findall(
            r"SALDO\s+INICIAL\s+DEPOSITOS\s+OTROS\s+ABONOS\s+CHEQUES\s+"
            r"OTROS\s+CARGOS\s+IMPUESTOS\s+SALDO\s+FINAL\s*\n[^\n]*?([\d.]+)\s*$",
            texto,
            re.I | re.M,
        )
        return normalizar_monto(matches[-1]) if matches else None
    return normalizar_monto(match.group(1)) if match else None


def _fecha_corta_periodo(valor: str, periodo_fin: date | None) -> date:
    dia, mes = (int(x) for x in valor.split("/"))
    year = periodo_fin.year if periodo_fin else timezone.localdate().year
    if periodo_fin and mes > periodo_fin.month:
        year -= 1
    return date(year, mes, dia)


def _movimiento_cuenta(fecha, descripcion, monto, cuenta, periodo):
    return _operacion(
        fecha=fecha,
        descripcion=descripcion,
        monto=monto,
        tarjeta="".join(re.findall(r"\d", cuenta))[-4:],
        periodo=periodo,
        tipo_banco="cartola_cuenta_corriente",
    )


def _deduplicar_movimientos(movimientos):
    unicos = {}
    for movimiento in movimientos:
        unicos.setdefault(movimiento.hash_externo(), movimiento)
    return list(unicos.values())


def _parsear_cuenta_itau(texto: str, cuenta: str, periodo_fin: date | None):
    movimientos = []
    patron = re.compile(
        rf"^\s*(\d{{2}}/\d{{2}})\s+\d+\s+(.+?)\s+\$\s*({_NUMERO})\s+"
        rf"\$\s*({_NUMERO})\s+[-$\d.]+\s*$",
        re.I,
    )
    for linea in texto.splitlines():
        match = patron.match(linea)
        if not match:
            continue
        cargo = normalizar_monto(match.group(3))
        abono = normalizar_monto(match.group(4))
        monto = cargo if cargo else -abono
        if monto:
            movimientos.append(_movimiento_cuenta(
                _fecha_corta_periodo(match.group(1), periodo_fin), match.group(2), monto,
                cuenta, periodo_fin,
            ))
    return movimientos


def _es_abono_santander(descripcion: str) -> bool:
    normalizada = normalizar_texto(descripcion)
    return bool(
        re.search(r"\btransf\.?\s+de\b|\btraspaso\s+internet\s+de\b", normalizada)
        or ("transf." in normalizada and "transf a" not in normalizada)
        or "remuneracion" in normalizada
        or ("transferencia" in normalizada and " de" in normalizada)
    )


def _parsear_cuenta_santander(texto: str, cuenta: str, periodo_fin: date | None):
    movimientos = []
    patron_fecha = re.compile(r"^\s*(\d{2}/\d{2})(.+)$")
    patron_valor = re.compile(r"(?:^|\s)(-?\d{1,3}(?:\.\d{3})+|-?\d{1,6})(?=\s|$)")
    for linea in texto.splitlines():
        match = patron_fecha.match(linea)
        if not match:
            continue
        resto = match.group(2).strip()
        valores = list(patron_valor.finditer(resto))
        if not valores:
            continue
        monto_match = valores[-2] if len(valores) >= 2 else valores[-1]
        monto = normalizar_monto(monto_match.group(1))
        descripcion = resto[: monto_match.start()].strip()
        descripcion = re.sub(r"^[A-Za-z.]+\s+", "", descripcion)
        descripcion = re.sub(r"^\d{7,12}\s+", "", descripcion)
        descripcion = re.sub(r"\s+\d{7,15}$", "", descripcion).strip()
        if not descripcion or descripcion.lower().startswith("resumen"):
            continue
        if _es_abono_santander(descripcion):
            monto = -monto
        movimientos.append(_movimiento_cuenta(
            _fecha_corta_periodo(match.group(1), periodo_fin), descripcion, monto,
            cuenta, periodo_fin,
        ))
    return _deduplicar_movimientos(movimientos)


def _parsear_cuenta_falabella(texto: str, cuenta: str, periodo_fin: date | None):
    movimientos = []
    patron = re.compile(
        rf"^\s*({_FECHA_LARGA})\s+.+?\s+(Transf.+?)\s+\$\s*({_NUMERO})"
        rf"(?:\s+\$\s*{_NUMERO})?\s*$",
        re.I,
    )
    for linea in texto.splitlines():
        match = patron.match(linea)
        if not match:
            continue
        descripcion = match.group(2)
        monto = normalizar_monto(match.group(3))
        if re.search(r"\btransf\.?\s+de\b", normalizar_texto(descripcion)):
            monto = -monto
        movimientos.append(_movimiento_cuenta(
            _fecha(match.group(1)), descripcion, monto, cuenta, periodo_fin,
        ))
    return movimientos


def _saldo_inicial_cuenta(texto: str) -> Decimal | None:
    match = re.search(r"SALDO\s+INICIAL\s*[: ]+(-?[\d.]+)", texto, re.I)
    return normalizar_monto(match.group(1)) if match else None


def _monto_con_saldo(valor: Decimal, saldo_anterior: Decimal | None, saldo_actual: Decimal,
                     descripcion: str) -> Decimal:
    """Resuelve cargo/abono cuando la cartola imprime una única columna de monto.

    Las cartolas de Scotiabank y BCI omiten la celda vacía al extraer texto. El
    saldo diario permite recuperar el signo sin depender de glosas bancarias;
    estas últimas quedan como fallback para la primera fila o redondeos.
    """
    if valor == 0:
        return Decimal("0")
    if saldo_anterior is not None:
        if saldo_anterior - valor == saldo_actual:
            return valor
        if saldo_anterior + valor == saldo_actual:
            return -valor
    normalizada = normalizar_texto(descripcion)
    if "toma deposito" in normalizada:
        return valor
    if re.search(r"\b(?:de|deposito|abono|remuneracion)\b", normalizada):
        return -valor
    return valor


def _parsear_cuenta_scotiabank(texto: str, cuenta: str, periodo_fin: date | None):
    movimientos = []
    saldo = _saldo_inicial_cuenta(texto)
    patron = re.compile(
        rf"^\s*({_FECHA_LARGA})\s+(.+?)\s+\d{{9}}\s+"
        rf"((?:-?[\d.]+)(?:\s+-?[\d.]+)?)\s*$",
        re.I,
    )
    for linea in texto.splitlines():
        match = patron.match(linea)
        if not match:
            continue
        valores = re.findall(r"-?[\d.]+", match.group(3))
        if len(valores) < 2:
            continue
        valor, saldo_actual = (normalizar_monto(item) for item in valores[-2:])
        monto = _monto_con_saldo(valor, saldo, saldo_actual, match.group(2))
        descripcion = match.group(2)
        if monto:
            movimientos.append(_movimiento_cuenta(
                _fecha(match.group(1)), descripcion, monto, cuenta, periodo_fin
            ))
        saldo = saldo_actual
    return _deduplicar_movimientos(movimientos)


def _filas_bci(texto: str):
    """Devuelve filas BCI con sus continuaciones de descripción.

    pdfplumber deja una línea de descripción antes y otra después de cada fila
    por la forma en que BCI ordena las columnas. Se usan los vecinos inmediatos,
    conservando sólo el cuerpo de movimientos entre el encabezado y el resumen.
    """
    lineas = texto.splitlines()
    indices = [i for i, linea in enumerate(lineas) if re.match(r"^\s*\d{2}/\d{2}/\d{4}\s+", linea)]
    patron = re.compile(
        rf"^\s*({_FECHA_LARGA})\s+(.+?)\s+(\d{{5,15}})\s+(-?[\d.]+)\s+(-?[\d.]+)\s*$",
        re.I,
    )
    for pos, indice in enumerate(indices):
        match = patron.match(lineas[indice])
        if not match:
            continue
        anterior = lineas[indice - 1].strip() if indice else ""
        siguiente = lineas[indice + 1].strip() if indice + 1 < len(lineas) else ""
        def es_continuacion(valor):
            return bool(valor and not re.match(r"^\s*\d{2}/\d{2}/\d{4}\s+", valor)
                        and not normalizar_texto(valor).startswith(("resumen", "total", "retenciones", "consideramos")))
        partes = [valor for valor in (anterior, siguiente) if es_continuacion(valor)]
        descripcion = " ".join(dict.fromkeys(partes)) or match.group(2)
        yield match.group(1), descripcion, normalizar_monto(match.group(4)), normalizar_monto(match.group(5))


def _parsear_cuenta_bci(texto: str, cuenta: str, periodo_fin: date | None):
    movimientos = []
    saldo = _saldo_inicial_cuenta(texto)
    if saldo is None:
        saldo = Decimal("0")
    for fecha, descripcion, valor, saldo_actual in _filas_bci(texto):
        monto = _monto_con_saldo(valor, saldo, saldo_actual, descripcion)
        if monto:
            movimientos.append(_movimiento_cuenta(
                _fecha(fecha), descripcion, monto, cuenta, periodo_fin
            ))
        saldo = saldo_actual
    return _deduplicar_movimientos(movimientos)


def _parsear_cuenta_corriente(texto: str, banco: str) -> EstadoCuentaParseado:
    cuenta = _detectar_numero_cuenta(texto, banco)
    if not cuenta:
        raise EstadoCuentaParseError("No se pudo identificar el número de la cuenta corriente.")
    periodo_inicio, periodo_fin = _detectar_periodo_cuenta(texto, banco)
    if banco == "Itaú":
        movimientos = _parsear_cuenta_itau(texto, cuenta, periodo_fin)
    elif banco == "Santander":
        movimientos = _parsear_cuenta_santander(texto, cuenta, periodo_fin)
    elif banco == "Scotiabank":
        movimientos = _parsear_cuenta_scotiabank(texto, cuenta, periodo_fin)
    elif banco == "BCI":
        movimientos = _parsear_cuenta_bci(texto, cuenta, periodo_fin)
    else:
        movimientos = _parsear_cuenta_falabella(texto, cuenta, periodo_fin)
    return EstadoCuentaParseado(
        banco=banco,
        tarjeta_ultimos4="".join(re.findall(r"\d", cuenta))[-4:],
        fecha_estado=periodo_fin,
        periodo_inicio=periodo_inicio,
        periodo_fin=periodo_fin,
        monto_facturado=None,
        monto_minimo=None,
        movimientos=movimientos,
        tipo_producto="CUENTA",
        numero_producto=cuenta,
        saldo_final_clp=_detectar_saldo_final_cuenta(texto, banco),
    )


def _detectar_monto(texto: str, etiqueta: str) -> Decimal | None:
    # No permitimos que el espacio del regex atraviese una línea y capture el día
    # de una fecha del cupón de pago en vez del monto real.
    match = re.search(rf"{etiqueta}[ \t]*\$?[ \t]*({_NUMERO})", texto, re.IGNORECASE)
    return normalizar_monto(match.group(1)) if match else None


def _detectar_monto_usd(texto: str, etiqueta: str) -> Decimal | None:
    match = re.search(
        rf"{etiqueta}[ \t]*US\$[ \t]*({_NUMERO})", texto, re.IGNORECASE
    )
    return normalizar_monto(match.group(1)) if match else None


def _detectar_cupos_usd(
    texto: str,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    match = re.search(
        rf"^\s*CUPO\s+TOTAL\s+US\$\s*({_NUMERO})\s+US\$\s*({_NUMERO})"
        rf"\s+US\$\s*({_NUMERO})\s*$",
        texto,
        re.IGNORECASE | re.MULTILINE,
    )
    if not match:
        return None, None, None
    return tuple(normalizar_monto(match.group(i)) for i in range(1, 4))


_CUPO_LABEL = re.compile(
    r"cupo\s+(?:compras\*?|(?:total\s+)?avance(?:\s+en\s+efectivo)?|total)\b",
    re.IGNORECASE,
)
_CUPO_TOKEN = re.compile(r"(?:-\s*|\$?\s*-?[\d.]+(?:,\d+)?)")


def _valores_cupo(linea: str) -> list[Decimal | None]:
    """Extrae las tres columnas total/utilizado/disponible de una fila de cupo."""
    resto = _CUPO_LABEL.sub("", linea, count=1).strip()
    tokens = _CUPO_TOKEN.findall(resto)[:3]
    valores = []
    for token in tokens:
        token = token.replace("$", "").strip()
        valores.append(None if token == "-" else normalizar_monto(token))
    return valores


def _detectar_cupos(texto: str) -> tuple[
    Decimal | None,
    Decimal | None,
    Decimal | None,
    Decimal | None,
]:
    """Detecta cupo principal y subcupo de avance en los formatos soportados."""
    principal: list[Decimal | None] = []
    avance: list[Decimal | None] = []
    for linea in texto.splitlines():
        normalizada = normalizar_texto(linea).strip()
        if normalizada.startswith("avance en") and not avance:
            tokens = _CUPO_TOKEN.findall(
                re.sub(r"^\s*avance\s+en\s*", "", linea, flags=re.IGNORECASE)
            )[:3]
            if len(tokens) >= 3:
                avance = [
                    normalizar_monto(token.replace("$", "").strip())
                    for token in tokens
                ]
            continue
        if not normalizada.startswith("cupo"):
            continue
        if re.match(r"^cupo\s+(?:total\s+)?avance\b", normalizada):
            if not avance:
                valores = _valores_cupo(linea)
                if len(valores) >= 3:
                    avance = valores
                elif valores:
                    # Lider Bci solo imprime el avance disponible; el porcentaje
                    # de CAE que sigue en la fila no representa otro cupo.
                    avance = [None, None, valores[0]]
        elif re.match(r"^cupo\s+(?:compras|total)\b", normalizada):
            valores = _valores_cupo(linea)
            if not principal and len(valores) >= 3:
                principal = valores
    principal += [None] * (3 - len(principal))
    avance += [None] * (3 - len(avance))
    return principal[0], principal[1], principal[2], avance[2]


def _operacion(
    *,
    fecha: date,
    descripcion: str,
    monto: Decimal,
    tarjeta: str,
    periodo: date | None,
    cuota_actual: int | None = None,
    cuota_total: int | None = None,
    moneda: str = "CLP",
    tipo_banco: str = "estado_cuenta_pdf",
) -> MovimientoNormalizado:
    descripcion = " ".join((descripcion or "").split()).strip(" -")[:255]
    return MovimientoNormalizado(
        fecha=fecha,
        descripcion=descripcion or "Operación sin descripción",
        monto=monto,
        moneda=moneda,
        estado="FACTURADO",
        tarjeta_id=tarjeta or None,
        cuota_actual=cuota_actual,
        cuota_total=cuota_total,
        periodo_factura=periodo,
        tipo_banco=tipo_banco,
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


def _parsear_santander_usd(
    texto: str, tarjeta: str, periodo: date | None
) -> list[MovimientoNormalizado]:
    movimientos = []
    patron = re.compile(
        rf"^\s*(\d{{2}}/\d{{2}}/\d{{2}})\s+(.+?)\s+({_NUMERO})\s+({_NUMERO})\s*$",
        re.IGNORECASE,
    )
    for linea in texto.splitlines():
        match = patron.match(linea)
        if not match:
            continue
        monto = normalizar_monto(match.group(4))
        if monto == 0:
            continue
        movimientos.append(
            _operacion(
                fecha=_fecha(match.group(1), corta=True),
                descripcion=match.group(2),
                monto=monto,
                moneda="USD",
                tarjeta=tarjeta,
                periodo=periodo,
            )
        )
    return movimientos


def _fragmento_descripcion_tenpo(linea: str) -> bool:
    normalizada = normalizar_texto(linea).strip()
    return bool(
        normalizada
        and "$" not in linea
        and not re.search(_FECHA_LARGA, linea)
        and not normalizada.startswith(
            ("total", "periodo", "estado de cuenta", "inf", "null")
        )
    )


def _parsear_tenpo(
    texto: str, tarjeta: str, periodo: date | None
) -> list[MovimientoNormalizado]:
    """Parsea filas Tenpo, incluyendo descripciones partidas alrededor de la fila."""
    movimientos = []
    lineas = texto.splitlines()
    for indice, linea in enumerate(lineas):
        fecha_match = re.search(_FECHA_LARGA, linea)
        if not fecha_match:
            continue
        resto = linea[fecha_match.end() :]
        monto_match = re.search(rf"\$\s*({_NUMERO})", resto)
        if not monto_match:
            continue
        monto = normalizar_monto(monto_match.group(1))
        if monto == 0:
            continue
        descripcion = resto[: monto_match.start()].strip()
        descripcion = re.sub(r"^null\s+", "", descripcion, flags=re.IGNORECASE)
        descripcion = re.sub(
            r"(?:^|\s+)(?:Digital|F[ií]sica)\s*$", "", descripcion, flags=re.IGNORECASE
        )
        if not descripcion:
            fragmentos = []
            if indice > 0 and _fragmento_descripcion_tenpo(lineas[indice - 1]):
                fragmentos.append(lineas[indice - 1].strip())
            if indice + 1 < len(lineas) and _fragmento_descripcion_tenpo(lineas[indice + 1]):
                fragmentos.append(lineas[indice + 1].strip())
            descripcion = " ".join(fragmentos)
        movimientos.append(
            _operacion(
                fecha=_fecha(fecha_match.group(0)),
                descripcion=descripcion,
                monto=monto,
                tarjeta=tarjeta,
                periodo=periodo,
            )
        )
    return movimientos


def _parsear_lider_bci(
    texto: str, tarjeta: str, periodo: date | None
) -> list[MovimientoNormalizado]:
    """Lee filas con lugar opcional, fecha, descripción y monto final en CLP."""
    movimientos = []
    patron = re.compile(
        rf"^\s*.*?({_FECHA_LARGA})\s+(.+?)\s+\$\s*({_NUMERO})\s*$",
        re.IGNORECASE,
    )
    for linea in texto.splitlines():
        match = patron.match(linea)
        if not match:
            continue
        monto = normalizar_monto(match.group(3))
        if monto == 0:
            continue
        descripcion = re.sub(r"\s*\(T\)\s*$", "", match.group(2), flags=re.IGNORECASE)
        movimientos.append(
            _operacion(
                fecha=_fecha(match.group(1)),
                descripcion=descripcion,
                monto=monto,
                tarjeta=tarjeta,
                periodo=periodo,
            )
        )
    return movimientos


def parsear_estado_cuenta_texto(texto: str, nombre_archivo: str = "") -> EstadoCuentaParseado:
    """Parsea texto extraído; es la entrada determinista usada por los tests."""
    banco = _detectar_banco(texto)
    if _es_cartola_cuenta_corriente(texto, banco):
        return _parsear_cuenta_corriente(texto, banco)
    tarjeta = _detectar_tarjeta(texto)
    fecha_estado = _detectar_fecha_estado(texto)
    fecha_vencimiento = _detectar_fecha_vencimiento(texto)
    periodo_inicio, periodo_fin = _detectar_periodo(texto)
    periodo = fecha_estado or periodo_fin
    es_santander_usd = banco == "Santander" and "internacional" in normalizar_texto(texto)
    if banco == "Itaú":
        movimientos = _parsear_itau(texto, tarjeta, periodo)
    elif banco == "Banco Falabella":
        movimientos = _parsear_falabella(texto, tarjeta, periodo)
    elif es_santander_usd:
        movimientos = _parsear_santander_usd(texto, tarjeta, periodo)
    elif banco == "Santander":
        movimientos = _parsear_santander(texto, tarjeta, periodo)
    elif banco == "Lider Bci":
        movimientos = _parsear_lider_bci(texto, tarjeta, periodo)
    else:
        movimientos = _parsear_tenpo(texto, tarjeta, periodo)
    if es_santander_usd:
        cupo_total = cupo_utilizado = cupo_disponible = cupo_avance_disponible = None
        cupo_total_usd, cupo_utilizado_usd, cupo_disponible_usd = _detectar_cupos_usd(texto)
        monto_facturado = _detectar_monto_usd(texto, r"DEUDA\s+TOTAL")
        monto_minimo = None
        moneda = "USD"
    else:
        cupo_total, cupo_utilizado, cupo_disponible, cupo_avance_disponible = _detectar_cupos(texto)
        cupo_total_usd = cupo_utilizado_usd = cupo_disponible_usd = None
        monto_facturado = _detectar_monto(
            texto,
            r"MONTO\s+TOTAL\s+FACTURADO"
            r"(?:\s+\(O\s+A\s+PAGAR\)|\s+A\s+PAGAR)?"
            r"(?:\s+\(A\+B\+C\+D\))?",
        )
        monto_minimo = _detectar_monto(texto, r"MONTO\s+M[IÍ]NIMO\s+A\s+PAGAR")
        moneda = "CLP"
    return EstadoCuentaParseado(
        banco=banco,
        tarjeta_ultimos4=tarjeta,
        fecha_estado=fecha_estado,
        fecha_vencimiento=fecha_vencimiento,
        periodo_inicio=periodo_inicio,
        periodo_fin=periodo_fin,
        monto_facturado=monto_facturado,
        monto_minimo=monto_minimo,
        movimientos=movimientos,
        moneda=moneda,
        cupo_total_clp=cupo_total,
        cupo_utilizado_clp=cupo_utilizado,
        cupo_disponible_clp=cupo_disponible,
        cupo_avance_disponible_clp=cupo_avance_disponible,
        cupo_total_usd=cupo_total_usd,
        cupo_utilizado_usd=cupo_utilizado_usd,
        cupo_disponible_usd=cupo_disponible_usd,
    )


_FECHA_DOCUMENTO = r"\b(?:\d{1,2}[./-]\d{1,2}[./-]\d{4}|\d{4}[./-]\d{1,2}[./-]\d{1,2})\b"
_FECHA_DOCUMENTO_NOMBRE = r"(?:\d{1,2}[./-]\d{1,2}[./-]\d{4}|\d{4}[./-]\d{1,2}[./-]\d{1,2})"


def _fecha_documento(valor: str) -> date | None:
    valor = (valor or "").strip().replace(".", "/").replace("-", "/")
    for formato in ("%d/%m/%Y", "%Y/%m/%d", "%d/%m/%y", "%m/%d/%y"):
        try:
            return datetime.strptime(valor, formato).date()
        except ValueError:
            continue
    return None


def _fecha_etiquetada(texto: str, etiquetas: tuple[str, ...]) -> date | None:
    """Busca una fecha en la línea de una etiqueta o en sus líneas vecinas."""
    lineas = texto.splitlines()
    etiquetas_norm = tuple(normalizar_texto(e) for e in etiquetas)
    for indice, linea in enumerate(lineas):
        bloque = " ".join(lineas[indice : indice + 3])
        if not any(etiqueta in normalizar_texto(linea) for etiqueta in etiquetas_norm):
            continue
        encontrada = re.search(_FECHA_DOCUMENTO, bloque)
        if encontrada:
            fecha = _fecha_documento(encontrada.group(0))
            if fecha:
                return fecha
    return None


def _monto_en_bloque(bloque: str) -> Decimal | None:
    # Se exige un separador de miles o al menos cuatro dígitos para no confundir
    # cuotas, roles y fechas con montos monetarios.
    candidatos = re.findall(
        r"\$?\s*-?(?:\d{1,3}(?:[. ]\d{3})+(?:,\d+)?|\d{4,}(?:,\d+)?)",
        bloque,
    )
    for candidato in candidatos:
        limpio = candidato.replace(" ", "")
        if re.fullmatch(r"\d{1,2}[./-]\d{1,2}[./-]\d{4}", limpio):
            continue
        if "$" not in candidato and len(re.sub(r"\D", "", limpio)) >= 7:
            # Los roles municipales suelen ser secuencias de 7 a 9 dígitos.
            continue
        try:
            monto = normalizar_monto(limpio)
        except ValueError:
            continue
        if monto >= 0:
            return monto
    return None


def _monto_etiquetado(texto: str, etiquetas: tuple[str, ...]) -> Decimal | None:
    lineas = texto.splitlines()
    etiquetas_norm = tuple(normalizar_texto(e) for e in etiquetas)
    for indice, linea in enumerate(lineas):
        if any(etiqueta in normalizar_texto(linea) for etiqueta in etiquetas_norm):
            for candidata in lineas[indice : indice + 2]:
                if candidata != linea and "rol" in normalizar_texto(candidata):
                    continue
                monto = _monto_en_bloque(candidata)
                if monto is not None:
                    return monto
    return None


def _rol_documento(valor: str) -> str:
    texto = str(valor or "").strip()
    # En los documentos de Concepción el formato visible 681-236 equivale al
    # rol numérico 6810236 que aparece en los nombres/PDF municipales.
    if re.fullmatch(r"\d{3}[-. ]\d{3}", texto):
        partes = re.split(r"[-. ]", texto)
        digitos = f"{partes[0]}0{partes[1]}"
    else:
        digitos = "".join(re.findall(r"\d", texto))
    return digitos.lstrip("0") or "0"


def _roles_documento(texto: str) -> list[str]:
    patrones = (
        r"\bROL(?:\s+(?:DE\s+)?(?:AVAL[UÚ]O|PROPIEDAD))?[^\d\n]{0,24}([0-9][0-9.\-]{5,})",
        r"\bROL[ \t]*[:#]?[ \t]*([0-9][0-9.\-]{5,})",
    )
    resultado = []
    for patron in patrones:
        for coincidencia in re.finditer(patron, texto, re.IGNORECASE):
            rol = _rol_documento(coincidencia.group(1))
            if len(rol) >= 6 and rol not in resultado:
                resultado.append(rol)
    return resultado


def _municipalidad_documento(texto: str) -> tuple[str, str]:
    for linea in texto.splitlines():
        limpia = " ".join(linea.split()).strip()
        normalizada = normalizar_texto(limpia)
        if "municipalidad" not in normalizada:
            continue
        municipio = limpia
        match = re.search(r"municipalidad\s+de\s+(.+)", normalizada, re.IGNORECASE)
        comuna = match.group(1).strip().title() if match else ""
        return municipio, comuna
    return "Municipalidad no identificada", ""


def _nombre_indica_documento_aseo(nombre_archivo: str) -> bool:
    """Reconoce nombres municipales aun cuando el PDF sea solo una imagen."""
    nombre = normalizar_texto(nombre_archivo)
    return bool(
        "aseo" in nombre
        or "contribucion" in nombre
        or "impuesto_territorial" in nombre
        or "orden_de_ingreso" in nombre
        or "orden_ingreso" in nombre
        or re.search(r"rol[_ -]*0?\d{7,9}", nombre)
        or re.search(r"\b\d{6,}_pago\b", nombre)
    )


def _parece_documento_aseo(texto: str, nombre_archivo: str = "") -> bool:
    normalizado = normalizar_texto(texto)
    if _nombre_indica_documento_aseo(nombre_archivo):
        return True
    # "municipalidad" puede aparecer en el comercio de una compra con tarjeta;
    # por sí sola no identifica una orden ni un comprobante municipal.
    return any(
        termino in normalizado
        for termino in (
            "derechos de aseo",
            "aseo domiciliario",
            "aseo extraccion",
            "contribuciones",
            "impuesto territorial",
            "orden de ingreso",
            "certificado de pago",
            "ccp2bpago.smc.cl",
        )
    )


def parsear_documento_aseo_texto(
    texto: str, nombre_archivo: str = ""
) -> DocumentoAseoParseado | None:
    """Reconoce órdenes y comprobantes municipales sin mezclarlos con cartolas."""
    normalizado = normalizar_texto(texto)
    nombre_normalizado = normalizar_texto(nombre_archivo)
    menciona_aseo = any(
        termino in normalizado
        for termino in ("derechos de aseo", "aseo domiciliario", "aseo extraccion")
    )
    marcador_nombre = _nombre_indica_documento_aseo(nombre_archivo)
    marcador_pago_municipal = (
        "certificado de pago" in normalizado
        and ("smc.cl" in normalizado or "bcoresultado" in normalizado)
    )
    if not menciona_aseo and (marcador_nombre or marcador_pago_municipal):
        menciona_aseo = True
    if not menciona_aseo:
        return None
    es_pago = (
        "certificado de pago" in normalizado
        or "comprobante de pago" in normalizado
        or "certificado_pago" in nombre_normalizado
        or ("pago" in nombre_normalizado and "orden de ingreso" not in normalizado)
    ) and ("pago" in normalizado or "pago" in nombre_normalizado)
    es_orden = (
        "orden de ingreso" in normalizado
        or "orden_de_ingreso" in nombre_normalizado
        or "orden_ingreso" in nombre_normalizado
    )
    if not es_pago and not es_orden:
        return None

    municipalidad, comuna = _municipalidad_documento(texto)
    roles = _roles_documento(texto)
    if not roles:
        # Una orden individual suele conservar el rol en el nombre aunque el OCR
        # haya omitido esa línea. Solo aceptamos la secuencia larga del rol.
        for candidato in re.findall(r"(?<!\d)0?\d{7,9}(?!\d)", nombre_archivo):
            rol = _rol_documento(candidato)
            if rol not in roles:
                roles.append(rol)
    if not roles and es_pago:
        # Los certificados municipales antiguos usan un identificador de seis
        # dígitos antes de ``_pago``; corresponde al rol 00-681-0-XXX de las
        # órdenes del mismo lote. Es un respaldo solo para ese patrón explícito.
        for candidato in re.findall(r"(?<!\d)(\d{6})[_ -]*pago", nombre_normalizado):
            rol = _rol_documento(f"00{candidato[:3]}0{candidato[3:]}")
            if rol not in roles:
                roles.append(rol)

    fechas = [_fecha_documento(valor) for valor in re.findall(_FECHA_DOCUMENTO, texto)]
    fechas += [
        _fecha_documento(valor)
        for valor in re.findall(r"\b\d{1,2}/\d{1,2}/\d{2}\b", texto)
    ]
    fechas = [valor for valor in fechas if valor]
    fechas_nombre = [
        _fecha_documento(valor)
        for valor in re.findall(_FECHA_DOCUMENTO_NOMBRE, nombre_archivo)
    ]
    fechas_nombre = [valor for valor in fechas_nombre if valor]
    fecha_emision = _fecha_etiquetada(
        texto, ("fecha de emision", "fecha emision", "emitido")
    )
    fecha_vencimiento = _fecha_etiquetada(
        texto, ("fecha de vencimiento", "vencimiento", "pagar hasta")
    )
    # No usar la palabra inglesa ``Date`` sin contexto: las firmas digitales
    # de una orden suelen incluirla y no representa la fecha de pago.
    fecha_pago = _fecha_etiquetada(texto, ("fecha de pago", "pagado el"))
    tiene_contexto_pago = any(
        etiqueta in normalizado
        for etiqueta in ("certificado de pago", "comprobante de pago", "fecha de pago", "pagado")
    )
    tiene_contexto_vencimiento = any(
        etiqueta in normalizado
        for etiqueta in ("fecha de vencimiento", "vencimiento", "pagar hasta")
    )
    tiene_contexto_emision = any(
        etiqueta in normalizado
        for etiqueta in ("fecha de emision", "fecha emision", "emitido")
    )
    if es_pago and not fecha_pago and fechas and tiene_contexto_pago:
        fecha_pago = max(fechas)
    if es_orden and not fecha_vencimiento and fechas and tiene_contexto_vencimiento:
        fecha_vencimiento = max(fechas)
    if not fecha_emision and fechas and tiene_contexto_emision:
        fecha_emision = min(fechas)
    if fechas_nombre:
        if es_pago and not fecha_pago:
            fecha_pago = max(fechas_nombre)
        if es_orden and not fecha_vencimiento:
            fecha_vencimiento = max(fechas_nombre)
        if not fecha_emision:
            fecha_emision = min(fechas_nombre)

    monto_base = _monto_etiquetado(
        texto, ("aseo extraccion domiciliaria", "derechos de aseo", "derecho de aseo")
    )
    monto_total = _monto_etiquetado(
        texto, ("total a pagar", "total pagar", "monto pagado", "monto", "total")
    )
    if es_pago and monto_total is None:
        monto_total = _monto_etiquetado(texto, ("webpay", "pago"))
    cuotas = re.search(r"\bCUOTA\s*(\d+)\s*[/ de]+\s*(\d+)\b", texto, re.IGNORECASE)
    if not cuotas:
        cuotas = re.search(
            r"CUOTA[_ -]*(\d+)\s*[-_/de]+\s*(\d+)",
            nombre_archivo,
            re.IGNORECASE,
        )
    referencia_match = re.search(
        r"(?:orden(?:\s+de)?\s+ingreso|comprobante|transacci[oó]n)\s*(?:n[°º#:]?\s*)?([A-Z0-9-]{4,})",
        texto,
        re.IGNORECASE,
    )
    if municipalidad == "Municipalidad no identificada" and (
        marcador_nombre or marcador_pago_municipal
    ):
        municipalidad = "Documento municipal"

    return DocumentoAseoParseado(
        tipo_documento="PAGO_ASEO" if es_pago else "ORDEN_ASEO",
        municipalidad=municipalidad,
        comuna=comuna,
        roles=roles,
        fecha_emision=fecha_emision,
        fecha_vencimiento=fecha_vencimiento,
        fecha_pago=fecha_pago,
        monto_base=monto_base,
        monto_total=monto_total,
        cuota_numero=int(cuotas.group(1)) if cuotas else None,
        cuotas_totales=int(cuotas.group(2)) if cuotas else None,
        referencia=referencia_match.group(1) if referencia_match else "",
    )


def parsear_estado_cuenta_pdf(
    contenido: bytes, nombre_archivo: str = ""
) -> EstadoCuentaParseado | DocumentoAseoParseado:
    texto = _extraer_texto_pdf(contenido)
    # Los documentos municipales ya no se importan desde Banca: Aseo y
    # contribuciones se registran manualmente en Pendientes. Conservamos el
    # parser histórico para no romper datos antiguos, pero no lo ejecutamos
    # durante una carga nueva.
    if _parece_documento_aseo(texto, nombre_archivo):
        raise EstadoCuentaParseError(
            "Los documentos de Aseo y contribuciones se registran manualmente en Pendientes; "
            "el importador de Banca solo acepta estados de cuenta bancarios."
        )
    return parsear_estado_cuenta_texto(texto, nombre_archivo)


def _leer_archivo(archivo) -> bytes:
    if isinstance(archivo, bytes):
        return archivo
    contenido = archivo.read()
    try:
        archivo.seek(0)
    except (AttributeError, OSError):
        pass
    return contenido


def _banco_existente(user, nombre: str) -> Banco:
    objetivo = normalizar_texto(nombre)
    bancos_usuario = list(Banco.objects.filter(
        credenciales__user=user,
        credenciales__tipo_fuente="banco",
    ).distinct())
    grupos = [
        bancos_usuario,
        list(Banco.objects.exclude(pk__in=[b.pk for b in bancos_usuario])),
    ]
    for bancos in grupos:
        for banco in bancos:
            if normalizar_texto(banco.nombre) == objetivo:
                return banco
        compatibles = [
            banco
            for banco in bancos
            if objetivo in normalizar_texto(banco.nombre)
            or normalizar_texto(banco.nombre) in objetivo
        ]
        if compatibles:
            # Entre "Lider" y "BCI", el nombre más específico evita depender
            # del orden incidental de la consulta.
            return max(compatibles, key=lambda banco: len(normalizar_texto(banco.nombre)))
    return Banco.objects.create(nombre=nombre, activo=True)


def _producto_tarjeta(
    user, banco: Banco, ultimos4: str, *, banco_detectado: str = ""
) -> Producto:
    from configuracion.services.productos import producto_canonico
    if ultimos4:
        existente = Producto.objects.filter(
            user=user, banco=banco, tipo="TDC", ultimos4=ultimos4
        ).first()
        if existente:
            return producto_canonico(existente)
    if "lider bci" in normalizar_texto(banco_detectado):
        for producto in Producto.objects.filter(user=user, banco=banco, tipo="TDC", activo=True):
            if "lider bci" in normalizar_texto(producto.nombre):
                if ultimos4 and ultimos4 not in producto.nombre:
                    producto.nombre = f"{producto.nombre} ••••{ultimos4}"
                    producto.ultimos4 = ultimos4
                    producto.origen = "IMPORTACION"
                    producto.save(update_fields=["nombre", "ultimos4", "origen"])
                return producto
    if normalizar_texto(banco_detectado) == "tenpo":
        productos = list(Producto.objects.filter(user=user, banco=banco, tipo="TDC", activo=True))
        if len(productos) == 1:
            producto = productos[0]
            if ultimos4 and ultimos4 not in producto.nombre:
                producto.nombre = f"{producto.nombre} ••••{ultimos4}"
                producto.ultimos4 = ultimos4
                producto.origen = "IMPORTACION"
                producto.save(update_fields=["nombre", "ultimos4", "origen"])
            return producto
    nombre = f"TDC {banco.nombre}" + (f" ••••{ultimos4}" if ultimos4 else " (PDF)")
    producto, _ = Producto.objects.get_or_create(
        user=user,
        banco=banco,
        nombre=nombre,
        defaults={"tipo": "TDC", "activo": True, "ultimos4": ultimos4, "origen": "IMPORTACION"},
    )
    return producto_canonico(producto)


def _producto_cuenta(user, banco: Banco, numero: str) -> Producto:
    from configuracion.services.productos import producto_canonico
    ultimos4 = "".join(re.findall(r"\d", numero))[-4:]
    existente = Producto.objects.filter(
        user=user, banco=banco, tipo="CUENTA", ultimos4=ultimos4
    ).first()
    if existente:
        return producto_canonico(existente)
    nombre = f"Cuenta corriente {banco.nombre} {numero}"
    producto, _ = Producto.objects.get_or_create(
        user=user,
        banco=banco,
        nombre=nombre,
        defaults={
            "tipo": "CUENTA",
            "activo": True,
            "ultimos4": ultimos4,
            "origen": "IMPORTACION",
        },
    )
    return producto_canonico(producto)


def _producto_resultado(user, banco: Banco, resultado: EstadoCuentaParseado) -> Producto:
    if resultado.tipo_producto == "CUENTA":
        return _producto_cuenta(user, banco, resultado.numero_producto)
    return _producto_tarjeta(
        user, banco, resultado.tarjeta_ultimos4, banco_detectado=resultado.banco
    )


def _guardar_snapshot(user, producto: Producto, resultado: EstadoCuentaParseado) -> None:
    if not resultado.fecha_estado:
        return
    valores = {"fecha_vencimiento": resultado.fecha_vencimiento}
    if resultado.tipo_producto == "CUENTA":
        valores["saldo_disponible_clp"] = resultado.saldo_final_clp
    elif resultado.moneda == "USD":
        valores.update(
            {
                "deuda_usd": resultado.monto_facturado,
                "cupo_usd": resultado.cupo_total_usd,
                "cupo_usd_disponible": resultado.cupo_disponible_usd,
            }
        )
        if not producto.tiene_cupo_usd:
            producto.tiene_cupo_usd = True
            producto.save(update_fields=["tiene_cupo_usd"])
    else:
        valores.update(
            {
                "deuda_facturada_clp": resultado.monto_facturado,
                "cupo_total_clp": resultado.cupo_total_clp,
                "cupo_utilizado_clp": resultado.cupo_utilizado_clp,
                "cupo_disponible_clp": resultado.cupo_disponible_clp,
                "cupo_avance_disponible_clp": resultado.cupo_avance_disponible_clp,
            }
        )
    defaults = {field: value for field, value in valores.items() if value is not None}
    if not defaults:
        return
    SaldoSnapshot.objects.update_or_create(
        user=user,
        producto=producto,
        fecha=resultado.fecha_estado,
        defaults=defaults,
    )


def _activar_estado_vigente(registro: ImportacionEstadoCuenta) -> None:
    """Deja una versión vigente por producto/mes de estado o vencimiento."""
    fecha_referencia = (
        registro.fecha_estado
        if registro.tipo_producto == "CUENTA"
        else registro.fecha_vencimiento
    )
    if not registro.producto_id or not fecha_referencia:
        registro.vigente = False
        registro.save(update_fields=["vigente"])
        return
    filtro_fecha = (
        {"fecha_estado__year": fecha_referencia.year, "fecha_estado__month": fecha_referencia.month}
        if registro.tipo_producto == "CUENTA"
        else {
            "fecha_vencimiento__year": fecha_referencia.year,
            "fecha_vencimiento__month": fecha_referencia.month,
        }
    )
    anteriores = ImportacionEstadoCuenta.objects.filter(
        user=registro.user,
        producto=registro.producto,
        moneda=registro.moneda,
        vigente=True,
        **filtro_fecha,
    ).exclude(pk=registro.pk).order_by("-created_at", "-id")
    anterior = anteriores.first()
    anteriores.update(vigente=False)
    registro.vigente = True
    registro.reemplaza_a = anterior
    campos = ["vigente", "reemplaza_a"]
    if registro.moneda == "USD" and registro.monto_facturado is not None:
        from configuracion.models import TipoCambio

        cambio = TipoCambio.objects.filter(fecha__lte=fecha_referencia).order_by("-fecha", "-id").first()
        cambio = cambio or TipoCambio.objects.order_by("-fecha", "-id").first()
        if cambio:
            registro.tipo_cambio_estimado = cambio.dolar
            registro.monto_facturado_clp_estimado = (
                registro.monto_facturado * cambio.dolar
            ).quantize(Decimal("1"))
            campos += ["tipo_cambio_estimado", "monto_facturado_clp_estimado"]
    registro.save(update_fields=campos)


def _departamento_por_rol(user, rol: str):
    from departamentos.models import Departamento

    objetivo = _rol_documento(rol)
    if objetivo == "0":
        return None
    for departamento in Departamento.objects.filter(user=user, activo=True):
        if _rol_documento(departamento.rol_sii) == objetivo:
            return departamento
    return None


def _regla_aseo_importada(user, departamento, documento: DocumentoAseoParseado, fecha_evento: date):
    from pendientes.models import ReglaPendiente

    clave = f"aseo:{departamento.pk}"
    monto_documento = documento.monto_total or documento.monto_base
    if documento.tipo_documento == "PAGO_ASEO" and len(documento.roles) > 1:
        # El certificado puede ser un Webpay agrupado; el total no es el monto
        # proyectable de cada propiedad.
        monto_documento = None
    regla, creada = ReglaPendiente.objects.get_or_create(
        user=user,
        clave_fuente=clave,
        defaults={
            "nombre": f"Aseo · {departamento.codigo}",
            "tipo": "PAGO",
            "departamento": departamento,
            "periodicidad": "TRIMESTRAL",
            "fecha_inicio": fecha_evento,
            "dia_vencimiento": fecha_evento.day,
            "monto_estimado": monto_documento,
            "url_pago": departamento.url_pago_aseo or reverse("dashboard_pendientes"),
            "canal_cobro": "DIRECTO",
            "recordar_dias": 3,
            "contabiliza_gasto": True,
            "automatica": False,
            "activa": True,
        },
    )
    cambios = []
    if regla.departamento_id != departamento.pk:
        regla.departamento = departamento
        cambios.append("departamento")
    if regla.monto_estimado is None and monto_documento:
        regla.monto_estimado = monto_documento
        cambios.append("monto_estimado")
    if not regla.url_pago:
        regla.url_pago = departamento.url_pago_aseo or reverse("dashboard_pendientes")
        cambios.append("url_pago")
    if cambios:
        cambios.append("actualizada_at")
        regla.save(update_fields=cambios)
    return regla


def _marcar_completada_importada(
    ocurrencia,
    registro: ImportacionEstadoCuenta,
    *,
    fecha_pago: date,
    monto: Decimal | None,
    referencia: str,
) -> None:
    from pendientes.models import AsignacionPagoPendiente

    valor = monto if monto and monto > 0 else None
    ocurrencia.estado = "COMPLETADA"
    ocurrencia.fecha_real = fecha_pago
    ocurrencia.monto_real = valor
    ocurrencia.completada_at = ocurrencia.completada_at or timezone.now()
    ocurrencia.nota_resolucion = "Confirmado por certificado de pago municipal importado."
    ocurrencia.importacion = registro
    ocurrencia.save(
        update_fields=[
            "estado",
            "fecha_real",
            "monto_real",
            "completada_at",
            "nota_resolucion",
            "importacion",
            "actualizada_at",
        ]
    )
    # Los certificados Webpay pueden agrupar varios roles. Solo se asigna un
    # monto por propiedad cuando proviene de una orden individual o cuando el
    # certificado contiene un único rol; así nunca se duplica el total global.
    if valor:
        asignacion, creada = AsignacionPagoPendiente.objects.get_or_create(
            ocurrencia=ocurrencia,
            importacion=registro,
            defaults={
                "monto_asignado": valor,
                "referencia_pago": referencia,
            },
        )
        if not creada and asignacion.monto_asignado != valor:
            asignacion.monto_asignado = valor
            asignacion.referencia_pago = referencia
            asignacion.save(update_fields=["monto_asignado", "referencia_pago"])


def _registrar_documento_aseo(
    user, registro: ImportacionEstadoCuenta, documento: DocumentoAseoParseado
) -> ImportacionEstadoCuenta:
    """Guarda una orden/comprobante y cruza sus roles con Pendientes."""
    from pendientes.models import OcurrenciaPendiente

    registro.tipo_documento = documento.tipo_documento
    registro.tipo_producto = "ASEO"
    registro.banco_detectado = documento.municipalidad
    registro.numero_producto = documento.roles[0] if documento.roles else ""
    registro.documento_referencia = documento.referencia
    registro.fecha_estado = documento.fecha_emision or documento.fecha_pago
    registro.fecha_vencimiento = documento.fecha_vencimiento
    registro.fecha_pago_real = documento.fecha_pago
    registro.monto_facturado = documento.monto_total
    registro.monto_pagado_clp = documento.monto_total if documento.tipo_documento == "PAGO_ASEO" else None
    registro.moneda = "CLP"
    registro.operaciones_detectadas = len(documento.roles)
    registro.estado = "PROCESADO"
    registro.procesado_at = timezone.now()
    registro.save()

    faltantes = []
    actualizadas = 0
    departamentos = []
    for rol in documento.roles:
        departamento = _departamento_por_rol(user, rol)
        if not departamento:
            faltantes.append(rol)
            continue
        departamentos.append(departamento)
        cambios_departamento = []
        if not departamento.comuna and documento.comuna:
            departamento.comuna = documento.comuna
            cambios_departamento.append("comuna")
        if not departamento.rol_sii:
            departamento.rol_sii = rol
            cambios_departamento.append("rol_sii")
        if cambios_departamento:
            cambios_departamento.append("updated_at")
            departamento.save(update_fields=cambios_departamento)

        fecha_evento = (
            documento.fecha_pago
            if documento.tipo_documento == "PAGO_ASEO"
            else documento.fecha_vencimiento
        ) or documento.fecha_emision or date.today()
        regla = _regla_aseo_importada(user, departamento, documento, fecha_evento)
        monto_orden = documento.monto_total or documento.monto_base or regla.monto_estimado
        if documento.tipo_documento == "PAGO_ASEO" and len(documento.roles) > 1:
            monto_orden = regla.monto_estimado

        if documento.tipo_documento == "ORDEN_ASEO":
            ocurrencia, creada = OcurrenciaPendiente.objects.get_or_create(
                regla=regla,
                fecha_vencimiento=fecha_evento,
                defaults={
                    "monto_esperado": monto_orden,
                    "importacion": registro,
                },
            )
            if not creada:
                if ocurrencia.estado == "PENDIENTE" and monto_orden is not None:
                    ocurrencia.monto_esperado = monto_orden
                if not ocurrencia.importacion_id:
                    ocurrencia.importacion = registro
                ocurrencia.save(update_fields=["monto_esperado", "importacion", "actualizada_at"])
            # Si el comprobante se cargó antes que la orden, la orden histórica
            # queda como evidencia informativa y no vuelve a abrir un pendiente.
            pago_previo = (
                regla.ocurrencias.filter(
                    estado="COMPLETADA",
                    importacion__tipo_documento="PAGO_ASEO",
                )
                .order_by("-fecha_real", "-id")
                .first()
            )
            if pago_previo and pago_previo.pk != ocurrencia.pk and ocurrencia.estado == "PENDIENTE":
                if pago_previo.monto_real is None and monto_orden and pago_previo.importacion_id:
                    _marcar_completada_importada(
                        pago_previo,
                        pago_previo.importacion,
                        fecha_pago=pago_previo.fecha_real or fecha_evento,
                        monto=monto_orden,
                        referencia=pago_previo.importacion.documento_referencia,
                    )
                ocurrencia.estado = "INCLUIDA_EN_OTRO_PAGO"
                ocurrencia.nota_resolucion = "La orden fue importada después de su comprobante de pago."
                ocurrencia.completada_at = ocurrencia.completada_at or timezone.now()
                ocurrencia.save(update_fields=["estado", "nota_resolucion", "completada_at", "actualizada_at"])
            actualizadas += 1
            continue

        # Primero intenta resolver la orden pendiente más cercana de ese rol.
        ocurrencia = (
            regla.ocurrencias.filter(estado="PENDIENTE", fecha_vencimiento__lte=fecha_evento)
            .order_by("-fecha_vencimiento", "-id")
            .first()
        )
        if ocurrencia is None:
            ocurrencia, _ = OcurrenciaPendiente.objects.get_or_create(
                regla=regla,
                fecha_vencimiento=fecha_evento,
                defaults={"monto_esperado": monto_orden},
            )
        if ocurrencia.monto_esperado is None and len(documento.roles) == 1:
            ocurrencia.monto_esperado = documento.monto_total
        monto = ocurrencia.monto_esperado
        if monto is None and len(documento.roles) == 1:
            monto = documento.monto_total
        _marcar_completada_importada(
            ocurrencia,
            registro,
            fecha_pago=documento.fecha_pago or fecha_evento,
            monto=monto,
            referencia=documento.referencia,
        )
        actualizadas += 1

    if len(departamentos) == 1:
        registro.departamento = departamentos[0]
    registro.operaciones_actualizadas = actualizadas
    registro.operaciones_nuevas = 0
    if faltantes:
        registro.mensaje = (
            "Documento de Aseo importado. No se encontraron propiedades para los roles: "
            + ", ".join(faltantes)
        )
    else:
        registro.mensaje = (
            "Documento de Aseo importado y cruzado con Pendientes. "
            "El certificado de pago silencia la obligación sin crear un movimiento duplicado."
        )
    registro.save(
        update_fields=[
            "departamento",
            "operaciones_actualizadas",
            "operaciones_nuevas",
            "mensaje",
        ]
    )
    return registro


@transaction.atomic
def importar_estado_cuenta(user, archivo, *, nombre_archivo: str | None = None) -> ImportacionEstadoCuenta:
    """Procesa un archivo; los errores quedan registrados y no abortan el lote."""
    contenido = _leer_archivo(archivo)
    nombre = nombre_archivo or getattr(archivo, "name", "estado_cuenta.pdf")
    huella = hashlib.sha256(contenido).hexdigest()
    existente = ImportacionEstadoCuenta.objects.filter(user=user, hash_archivo=huella).first()
    if existente and existente.estado == 'PROCESADO':
        # Conservamos el registro original como PROCESADO; la huella evita crear
        # una segunda importación y la ingesta sigue siendo idempotente.
        existente.fue_duplicado = True
        return existente

    registro = existente or ImportacionEstadoCuenta(
        user=user,
        nombre_archivo=nombre[:255],
        hash_archivo=huella,
        estado="ERROR",
    )
    registro.fue_duplicado = False
    if not existente:
        registro.archivo.save(nombre[:255], ContentFile(contenido), save=False)
    registro.save()
    try:
        # A failed later stage must undo movements, aliases, document validity,
        # provisional closure, and snapshots while retaining the error record.
        with transaction.atomic():
            resultado = parsear_estado_cuenta_pdf(contenido, nombre)
            if isinstance(resultado, DocumentoAseoParseado):
                return _registrar_documento_aseo(user, registro, resultado)
            banco = _banco_existente(user, resultado.banco)
            producto = _producto_resultado(user, banco, resultado)
            ingesta = ingestar_movimientos(
                user,
                banco,
                resultado.movimientos,
                origen="PDF",
                tipo_producto=resultado.tipo_producto,
                nombre_producto=producto.nombre,
            )
            registro.banco = banco
            registro.producto = producto
            registro.estado = "PROCESADO"
            registro.banco_detectado = resultado.banco
            registro.tarjeta_ultimos4 = resultado.tarjeta_ultimos4
            registro.tipo_producto = resultado.tipo_producto
            registro.numero_producto = resultado.numero_producto
            registro.saldo_final_clp = resultado.saldo_final_clp
            registro.fecha_estado = resultado.fecha_estado
            registro.fecha_vencimiento = resultado.fecha_vencimiento
            registro.periodo_inicio = resultado.periodo_inicio
            registro.periodo_fin = resultado.periodo_fin
            registro.monto_facturado = resultado.monto_facturado
            registro.monto_minimo = resultado.monto_minimo
            registro.moneda = resultado.moneda
            registro.cupo_total_clp = resultado.cupo_total_clp
            registro.cupo_utilizado_clp = resultado.cupo_utilizado_clp
            registro.cupo_disponible_clp = resultado.cupo_disponible_clp
            registro.cupo_avance_disponible_clp = resultado.cupo_avance_disponible_clp
            registro.cupo_total_usd = resultado.cupo_total_usd
            registro.cupo_utilizado_usd = resultado.cupo_utilizado_usd
            registro.cupo_disponible_usd = resultado.cupo_disponible_usd
            registro.operaciones_detectadas = len(resultado.movimientos)
            registro.operaciones_nuevas = ingesta["nuevos"]
            registro.operaciones_actualizadas = ingesta["actualizados"]
            registro.mensaje = "Análisis completado."
            registro.procesado_at = timezone.now()
            registro.save()
            _activar_estado_vigente(registro)
            registro.movimientos.add(*ingesta["ids"])
            from banca.services.provisionales import cerrar_periodo_apple_pay

            cierre = cerrar_periodo_apple_pay(
                user,
                producto,
                resultado.periodo_inicio,
                resultado.periodo_fin,
                list(
                    MovimientoBancario.objects.filter(
                        user=user,
                        id__in=ingesta["ids"],
                        origen="PDF",
                    )
                ),
                registro,
            )
            registro.provisionales_confirmados = cierre["confirmados"]
            registro.provisionales_descartados = cierre["descartados"]
            registro.provisionales_ambiguos = cierre["ambiguos"]
            registro.operaciones_nuevas = max(
                0, registro.operaciones_nuevas - cierre["confirmados"]
            )
            registro.operaciones_actualizadas += cierre["confirmados"]
            if cierre["sin_periodo"]:
                registro.mensaje = (
                    "Análisis completado; el PDF no informó período y no cerró compras Apple Pay."
                )
            registro.save(
                update_fields=[
                    "provisionales_confirmados",
                    "provisionales_descartados",
                    "provisionales_ambiguos",
                    "operaciones_nuevas",
                    "operaciones_actualizadas",
                    "mensaje",
                ]
            )
            _guardar_snapshot(user, producto, resultado)
            # El orden de carga no debe importar: si NeatPagos fue importado antes,
            # el estado recién procesado completa ahora el cruce por monto y fecha.
            from banca.services.fuentes_externas import conciliar_eventos

            conciliar_eventos(user)
    except Exception as exc:
        registro.refresh_from_db()
        registro.estado = 'ERROR'
        registro.mensaje = str(exc)[:1000]
        registro.save()
    return registro


@transaction.atomic
def reanalizar_importacion(user, importacion: ImportacionEstadoCuenta) -> ImportacionEstadoCuenta:
    """Reaplica el parser al archivo guardado sin crear otra importación ni borrar datos."""
    if importacion.user_id != user.id:
        raise ValueError("La importación no pertenece al usuario.")
    with importacion.archivo.open("rb") as archivo:
        contenido = archivo.read()
    resultado = parsear_estado_cuenta_pdf(contenido, importacion.nombre_archivo)
    if isinstance(resultado, DocumentoAseoParseado):
        return _registrar_documento_aseo(user, importacion, resultado)
    banco = _banco_existente(user, resultado.banco)
    producto = _producto_resultado(user, banco, resultado)
    ingesta = ingestar_movimientos(
        user,
        banco,
        resultado.movimientos,
        origen="PDF",
        tipo_producto=resultado.tipo_producto,
        nombre_producto=producto.nombre,
    )

    importacion.banco = banco
    importacion.producto = producto
    importacion.estado = "PROCESADO"
    importacion.banco_detectado = resultado.banco
    importacion.tarjeta_ultimos4 = resultado.tarjeta_ultimos4
    importacion.tipo_producto = resultado.tipo_producto
    importacion.numero_producto = resultado.numero_producto
    importacion.saldo_final_clp = resultado.saldo_final_clp
    importacion.fecha_estado = resultado.fecha_estado
    importacion.fecha_vencimiento = resultado.fecha_vencimiento
    importacion.periodo_inicio = resultado.periodo_inicio
    importacion.periodo_fin = resultado.periodo_fin
    importacion.monto_facturado = resultado.monto_facturado
    importacion.monto_minimo = resultado.monto_minimo
    importacion.moneda = resultado.moneda
    importacion.cupo_total_clp = resultado.cupo_total_clp
    importacion.cupo_utilizado_clp = resultado.cupo_utilizado_clp
    importacion.cupo_disponible_clp = resultado.cupo_disponible_clp
    importacion.cupo_avance_disponible_clp = resultado.cupo_avance_disponible_clp
    importacion.cupo_total_usd = resultado.cupo_total_usd
    importacion.cupo_utilizado_usd = resultado.cupo_utilizado_usd
    importacion.cupo_disponible_usd = resultado.cupo_disponible_usd
    importacion.operaciones_detectadas = len(resultado.movimientos)
    importacion.operaciones_nuevas = ingesta["nuevos"]
    importacion.operaciones_actualizadas = ingesta["actualizados"]
    importacion.mensaje = "Archivo reanalizado con el parser actual."
    importacion.procesado_at = timezone.now()
    importacion.save()
    _activar_estado_vigente(importacion)
    importacion.movimientos.add(*ingesta["ids"])

    from banca.services.provisionales import cerrar_periodo_apple_pay

    cierre = cerrar_periodo_apple_pay(
        user,
        producto,
        resultado.periodo_inicio,
        resultado.periodo_fin,
        list(MovimientoBancario.objects.filter(user=user, id__in=ingesta["ids"])),
        importacion,
    )
    importacion.provisionales_confirmados += cierre["confirmados"]
    importacion.provisionales_descartados += cierre["descartados"]
    importacion.provisionales_ambiguos += cierre["ambiguos"]
    importacion.operaciones_nuevas = max(
        0, importacion.operaciones_nuevas - cierre["confirmados"]
    )
    importacion.operaciones_actualizadas += cierre["confirmados"]
    if cierre["sin_periodo"]:
        importacion.mensaje += " El PDF no informó período; no se cerraron provisionales."
    importacion.save(
        update_fields=[
            "provisionales_confirmados",
            "provisionales_descartados",
            "provisionales_ambiguos",
            "operaciones_nuevas",
            "operaciones_actualizadas",
            "mensaje",
        ]
    )
    _guardar_snapshot(user, producto, resultado)
    from banca.services.fuentes_externas import conciliar_eventos

    conciliar_eventos(user)
    return importacion
