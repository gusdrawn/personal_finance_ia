"""
Connector de Santander Chile.

Estrategia (ver docs/ARQUITECTURA_INTEGRACION_BANCARIA.md §13): Playwright autentica y
NAVEGA el SPA privado; los datos se obtienen **interceptando las respuestas JSON** del
gateway `https://api-dsk.santander.cl/perdsk/` (no se raspa el DOM).

Endpoints confirmados en recon (09-06-2026):
  - tarjetasDeCredito/consultaUltimosMovimientos   -> movimientos NO facturados
  - tarjetasDeCredito/estadoDeCuenta               -> estado de cuenta (facturado)
  - serviciosCliente/creditoDeConsumo/deudaPendientePrestamo -> deuda crédito/hipotecario
  - (saldos/cupos)                                 -> consolidado al login

NOTA: los NOMBRES EXACTOS de los campos dentro de cada JSON deben confirmarse en la
primera corrida real (el recon expuso los endpoints, no el cuerpo). Por eso el parseo es
defensivo: busca el primer campo que calce entre varios candidatos y registra en logs lo
que no reconoce, sin reventar.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from bank_sync.connectors.base import BankConnector
from bank_sync.exceptions import (
    DeviceVerificationRequired,
    ExtractionError,
    LoginError,
    ParseError,
)
from bank_sync.models import (
    DeudaResumen,
    EstadoCuenta,
    MovimientoNormalizado,
    SaldoHipotecario,
    SaldoTarjeta,
)

logger = logging.getLogger("bank_sync.santander")

API_HOST = "api-dsk.santander.cl"
API_BASE = f"https://{API_HOST}/perdsk"

EP_NO_FACTURADO = "tarjetasDeCredito/consultaUltimosMovimientos"
EP_ESTADO_CUENTA = "tarjetasDeCredito/estadoDeCuenta"
EP_DEUDA_CREDITO = "serviciosCliente/creditoDeConsumo/deudaPendientePrestamo"

FRAME = "https://mibanco.santander.cl/UI.Web.HB/Private_new/frame/#/private"
ROUTE_SALDOS = f"{FRAME}/Saldos_TC/main/detail"
ROUTE_NO_FACTURADO = f"{FRAME}/Saldos_TC/main/bill"
ROUTE_FACTURADO = f"{FRAME}/Saldos_TC/main/billed"
ROUTE_CREDITOS = f"{FRAME}/creditos/main/list-creditos"

LOGIN_URL = "https://banco.santander.cl/personas"
# El formulario de login vive en un iframe Angular cross-origin (mibanco.santander.cl).
# Se puede navegar directo a la ruta del frame para renderizar RUT/clave como top-level.
LOGIN_FRAME_URL = (
    "https://mibanco.santander.cl/UI.Web.HB/Private_new/frame/#/public/login-frame/ing/0010"
)
# Selectores reales (Angular Material; el atributo name viene vacío, usar id/formcontrolname).
SEL_RUT = "input#rut, input[formcontrolname='txtUser']"
SEL_CLAVE = "input#pass, input[formcontrolname='txtPass'], input[type='password']"
SEL_SUBMIT = "button[type='submit']"


# --------------------------------------------------------------------------- helpers
def _to_decimal(value: Any) -> Decimal:
    """Convierte montos en formato chileno ('1.912.943' / '-9.390' / 11700.00) a Decimal."""
    if value is None:
        return Decimal("0")
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return Decimal("0")
    s = str(value).strip()
    if not s:
        return Decimal("0")
    s = s.replace("$", "").replace("USD", "").replace("US$", "").strip()
    neg = s.startswith("-") or s.startswith("(")
    s = re.sub(r"[^\d,.\-]", "", s)
    # formato chileno: '.' miles, ',' decimal
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")        # 1.234.567,89 -> 1234567.89
    elif "," in s:
        s = s.replace(",", ".")                          # 1234,56 -> 1234.56
    elif s.count(".") > 1:
        s = s.replace(".", "")                           # 1.234.567 -> 1234567 (miles)
    elif "." in s:
        ent, frac = s.replace("-", "").rsplit(".", 1)
        # un solo punto con 3 decimales en pesos = separador de miles ('9.390' -> 9390);
        # 1, 2 o 4+ dígitos se tratan como decimal real ('11700.00', '117.5').
        if len(frac) == 3:
            s = ent + frac
    s = s.replace("-", "")
    try:
        d = Decimal(s or "0")
    except InvalidOperation:
        return Decimal("0")
    return -d if neg else d


def _monto_escalar(value: Any, escala: int = 10000) -> Decimal:
    """
    Campos monetarios del crédito vienen como '+0000000019765517' con 4 decimales implícitos.
    Ej.: MontoCapVigente -> /10000 = 1976.5517 UF ; ValCuotaVigML -> /10000 = 458984 CLP.
    """
    if value is None:
        return Decimal("0")
    t = str(value).strip()
    neg = t.startswith("-")
    t = re.sub(r"\D", "", t)
    if not t:
        return Decimal("0")
    d = Decimal(t) / Decimal(escala)
    return -d if neg else d


def _to_date(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, (date, datetime)):
        return value.date() if isinstance(value, datetime) else value
    s = str(value).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y%m%d", "%d/%m/%y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _first(d: dict, *keys, default=None):
    """Devuelve el primer valor presente entre varias claves candidatas (case-insensitive)."""
    if not isinstance(d, dict):
        return default
    lower = {k.lower(): v for k, v in d.items()}
    for k in keys:
        if k.lower() in lower and lower[k.lower()] not in (None, ""):
            return lower[k.lower()]
    return default


def _find_rows(payload: Any) -> list[dict]:
    """Busca recursivamente la primera lista de dicts 'larga' dentro del JSON."""
    best: list[dict] = []

    def walk(node):
        nonlocal best
        if isinstance(node, list):
            dicts = [x for x in node if isinstance(x, dict)]
            if len(dicts) > len(best):
                best = dicts
            for x in node:
                walk(x)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)

    walk(payload)
    return best


def _detect_cuotas(texto: str) -> tuple[Optional[int], Optional[int]]:
    m = re.search(r"(\d+)\s*/\s*(\d+)", texto or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None


# --------------------------------------------------------------------------- connector
class SantanderConnector(BankConnector):
    nombre = "santander"

    def __init__(self, *, headless: bool = True, storage_state_path: Optional[str] = None,
                 timeout_ms: int = 30000):
        super().__init__(headless=headless, storage_state_path=storage_state_path)
        self.timeout_ms = timeout_ms
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        # buffer de respuestas interceptadas: endpoint_path -> lista de JSON
        self._responses: dict[str, list[Any]] = {}
        # si está activo, vuelca el JSON crudo de cada respuesta del gateway para depurar parsers
        import os

        self._dump = bool(os.environ.get("BANK_SYNC_DUMP_RESPONSES"))

    # ---- infraestructura Playwright ----
    def _ensure_page(self):
        if self._page is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # pragma: no cover
            raise ExtractionError(
                "Falta Playwright. Instala con: pip install playwright && playwright install chromium"
            ) from e

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        ctx_kwargs: dict = {"locale": "es-CL"}
        if self.storage_state_path:
            import os

            if os.path.exists(self.storage_state_path):
                ctx_kwargs["storage_state"] = self.storage_state_path
        self._context = self._browser.new_context(**ctx_kwargs)
        self._context.set_default_timeout(self.timeout_ms)
        self._page = self._context.new_page()
        self._page.on("response", self._capture_response)

    def _capture_response(self, response):
        url = response.url
        if API_HOST not in url:
            return
        try:
            if "application/json" not in (response.headers.get("content-type", "")):
                return
            data = response.json()
        except Exception:
            return
        # clave = path relativo al gateway
        path = url.split("/perdsk/", 1)[-1].split("?")[0]
        self._responses.setdefault(path, []).append(data)
        logger.debug("Interceptado %s", path)

    def _wait_for(self, endpoint_path: str, *, since: int = 0, timeout_ms: Optional[int] = None) -> list[Any]:
        """Espera a que se haya interceptado al menos una respuesta nueva del endpoint."""
        import time

        deadline = time.time() + (timeout_ms or self.timeout_ms) / 1000.0
        while time.time() < deadline:
            bucket = self._responses.get(endpoint_path, [])
            if len(bucket) > since:
                return bucket[since:]
            self._page.wait_for_timeout(250)
        return self._responses.get(endpoint_path, [])[since:]

    def _goto(self, url: str):
        self._page.goto(url, wait_until="networkidle")

    # ---- ciclo de vida ----
    def login(self, rut: str, clave: str) -> None:
        self._ensure_page()
        try:
            page = self._page
            # El login es un SPA Angular dentro de un iframe cross-origin. Navegar directo a la
            # ruta del frame lo renderiza como documento top-level (más estable que el iframe).
            self._goto(LOGIN_FRAME_URL)
            page.wait_for_timeout(1500)

            # Campos RUT / Clave: en el documento o en cualquier iframe (por si vuelve a embeber).
            rut_input = self._find_input(SEL_RUT)
            clave_input = self._find_input(SEL_CLAVE)
            if rut_input is None or clave_input is None:
                # Fallback: pasar por el home y abrir el panel "Ingresar"
                self._goto(LOGIN_URL)
                try:
                    page.get_by_role("link", name=re.compile("Ingresar", re.I)).first.click(timeout=5000)
                except Exception:
                    pass
                page.wait_for_timeout(1500)
                rut_input = self._find_input(SEL_RUT)
                clave_input = self._find_input(SEL_CLAVE)
            if rut_input is None or clave_input is None:
                self._dump_debug("login_no_fields")
                raise LoginError(
                    "No se encontraron los campos de RUT/clave en el login. "
                    "Revisa los artefactos en ~/.bank_sync/debug (screenshot/HTML/inputs.json) "
                    "para afinar los selectores."
                )
            rut_input.fill(rut, timeout=self.timeout_ms)
            clave_input.fill(clave, timeout=self.timeout_ms)
            # Click en "Ingresar" (submit) o Enter como respaldo
            submit = self._find_input(SEL_SUBMIT)
            if submit is not None:
                submit.click(timeout=self.timeout_ms)
            else:
                clave_input.press("Enter")

            # Esperar a aterrizar en el área privada. OJO: la URL del login ya contiene
            # "Private_new"; lo que cambia al autenticar es el hash de ruta: #/public/... -> #/private.
            try:
                page.wait_for_url(re.compile(r"#/private"), timeout=self.timeout_ms)
            except Exception:
                # ¿pide verificación de dispositivo?
                body = (page.content() or "").lower()
                if any(k in body for k in ("dispositivo", "verificaci", "código", "token")):
                    self._dump_debug("login_device")
                    raise DeviceVerificationRequired(
                        "Santander pide validar el dispositivo; requiere intervención manual."
                    )
                self._dump_debug("login_no_private")
                raise LoginError("No se llegó al área privada tras el login (¿credenciales?).")

            if self.storage_state_path:
                self._context.storage_state(path=self.storage_state_path)
        except DeviceVerificationRequired:
            raise
        except LoginError:
            raise
        except Exception as e:
            self._dump_debug("login_error")
            raise LoginError(f"Fallo de login en Santander: {e}") from e

    def _find_input(self, selector: str):
        """Busca el primer input que calce, en el documento principal o en cualquier iframe."""
        page = self._page
        # 1. documento principal
        loc = page.locator(selector).first
        try:
            if loc.count() > 0:
                return loc
        except Exception:
            pass
        # 2. iframes
        for fr in page.frames:
            if fr == page.main_frame:
                continue
            try:
                floc = fr.locator(selector).first
                if floc.count() > 0:
                    return floc
            except Exception:
                continue
        return None

    def _dump_debug(self, prefix: str = "login_fail") -> Optional[str]:
        """Guarda screenshot + HTML de la página actual para depurar (§8). Devuelve el path base."""
        if not self._page:
            return None
        import os
        import time

        base_dir = os.environ.get("BANK_SYNC_DEBUG_DIR") or os.path.expanduser("~/.bank_sync/debug")
        os.makedirs(base_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        base = os.path.join(base_dir, f"{prefix}_{stamp}")
        try:
            self._page.screenshot(path=f"{base}.png", full_page=True)
        except Exception:
            pass
        try:
            with open(f"{base}.html", "w", encoding="utf-8") as fh:
                fh.write(self._page.content() or "")
        except Exception:
            pass
        # volcado de inputs detectados (incluye iframes) para afinar selectores
        try:
            campos = []
            for fr in self._page.frames:
                try:
                    fi = fr.eval_on_selector_all(
                        "input",
                        "els=>els.map(e=>({name:e.name,id:e.id,type:e.type,placeholder:e.placeholder}))",
                    )
                except Exception:
                    fi = []
                for f in fi:
                    f["frame"] = fr.url[:80]
                    campos.append(f)
            import json

            with open(f"{base}.inputs.json", "w", encoding="utf-8") as fh:
                json.dump(campos, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass
        logger.warning("Artefactos de depuración guardados en %s.*", base)
        return base

    def _dump_responses(self, tag: str = "responses") -> Optional[str]:
        """Vuelca TODO el buffer de respuestas interceptadas del gateway (para afinar parsers)."""
        if not self._dump or not self._responses:
            return None
        import json
        import os
        import time

        base_dir = os.environ.get("BANK_SYNC_DEBUG_DIR") or os.path.expanduser("~/.bank_sync/debug")
        os.makedirs(base_dir, exist_ok=True)
        path = os.path.join(base_dir, f"{tag}_{time.strftime('%Y%m%d_%H%M%S')}.json")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self._responses, fh, ensure_ascii=False, indent=2, default=str)
            logger.warning("Respuestas crudas del gateway guardadas en %s (paths: %s)",
                           path, list(self._responses.keys()))
        except Exception as e:  # pragma: no cover
            logger.warning("No se pudo volcar respuestas: %s", e)
        return path

    def logout(self) -> None:
        for closer in (self._context, self._browser):
            try:
                if closer:
                    closer.close()
            except Exception:
                pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self._context = self._page = None

    # ---- datos ----
    def get_deudas(self) -> DeudaResumen:
        self._ensure_page()
        resumen = DeudaResumen()
        # El home dispara la consulta consolidada de productos/cupos (se cachea tras el login).
        try:
            self._goto(f"{FRAME}/home/main")
            self._page.wait_for_timeout(2500)
        except Exception:
            pass
        # Saldos/cupos: se disparan al cargar la vista de tarjetas
        before = {k: len(v) for k, v in self._responses.items()}
        self._goto(ROUTE_SALDOS)
        self._page.wait_for_timeout(2500)
        for path, bucket in self._responses.items():
            if "tarjeta" not in path.lower():
                continue
            for data in bucket[before.get(path, 0):]:
                resumen.tarjetas.extend(self._parse_saldos_tarjetas(data))

        # Crédito / hipotecario
        n0 = len(self._responses.get(EP_DEUDA_CREDITO, []))
        self._goto(ROUTE_CREDITOS)
        for data in self._wait_for(EP_DEUDA_CREDITO, since=n0, timeout_ms=12000):
            cred = self._parse_credito(data)
            if cred:
                resumen.creditos.append(cred)
        self._dump_responses("deudas")
        return resumen

    def get_movimientos_no_facturados(self) -> list[MovimientoNormalizado]:
        self._ensure_page()
        n0 = len(self._responses.get(EP_NO_FACTURADO, []))
        self._goto(ROUTE_NO_FACTURADO)
        movs: list[MovimientoNormalizado] = []
        for data in self._wait_for(EP_NO_FACTURADO, since=n0):
            movs.extend(self._parse_movimientos(data, estado="NO_FACTURADO"))
        self._dump_responses("no_facturado")
        return movs

    def get_estado_cuenta(self, periodo: Optional[date] = None) -> list[EstadoCuenta]:
        self._ensure_page()
        n0 = len(self._responses.get(EP_ESTADO_CUENTA, []))
        self._goto(ROUTE_FACTURADO)
        estados: list[EstadoCuenta] = []
        for data in self._wait_for(EP_ESTADO_CUENTA, since=n0):
            ec = self._parse_estado_cuenta(data, periodo)
            if ec:
                estados.append(ec)
        return estados

    # ---- parsers (defensivos; confirmar nombres de campos en 1ª corrida) ----
    def _parse_saldos_tarjetas(self, data: Any) -> list[SaldoTarjeta]:
        out: list[SaldoTarjeta] = []
        for row in _find_rows(data) or ([data] if isinstance(data, dict) else []):
            tid = _first(row, "numeroTarjeta", "tarjeta", "ultimos4", "pan", default="")
            tid = re.sub(r"\D", "", str(tid))[-4:] if tid else ""
            if not tid and not _first(row, "cupoTotal", "cupoAutorizado", "montoAutorizado"):
                continue
            out.append(
                SaldoTarjeta(
                    tarjeta_id=tid,
                    nombre=str(_first(row, "nombreTarjeta", "descripcion", "marca", default="Tarjeta")),
                    cupo_total_clp=_to_decimal(_first(row, "cupoAutorizadoPesos", "cupoTotal", "montoAutorizado")),
                    cupo_disponible_clp=_to_decimal(_first(row, "cupoDisponiblePesos", "cupoDisponible", "disponible")),
                    deuda_no_facturada_clp=_to_decimal(_first(row, "utilizadoPesos", "montoUtilizado", "utilizado")),
                    tiene_cupo_usd=bool(_first(row, "cupoAutorizadoDolar", "cupoDolar", "cupoUsd")),
                    cupo_usd=_to_decimal(_first(row, "cupoAutorizadoDolar", "cupoUsd")) or None,
                    cupo_usd_disponible=_to_decimal(_first(row, "cupoDisponibleDolar", "disponibleUsd")) or None,
                    deuda_usd=_to_decimal(_first(row, "utilizadoDolar", "utilizadoUsd")) or None,
                    tipo_cambio_banco=_to_decimal(_first(row, "tipoCambio", "valorDolar")) or None,
                )
            )
        return out

    def _parse_credito(self, data: Any) -> Optional[SaldoHipotecario]:
        """
        Estructura real Santander (deudaPendientePrestamo -> DATA.Escalares), montos con 4
        decimales implícitos y divisa propia:
          Divisa, MontoCapVigente (saldo, UF), ValCuotaVigML (cuota CLP), ValCuotaVigMO (cuota UF),
          TasaInteres (4500000=4.5%), TotalCuotas, CuotasPagadas, FecVencimiento, SituacionDeuda.
        """
        esc = None
        if isinstance(data, dict):
            d = data.get("DATA") or data.get("Data") or data
            if isinstance(d, dict):
                esc = d.get("Escalares") or d.get("ESCALARES")
        if not isinstance(esc, dict):
            # fallback al esquema genérico (por si otro producto difiere)
            rows = _find_rows(data)
            row = rows[0] if rows else (data if isinstance(data, dict) else None)
            if not row:
                return None
            return SaldoHipotecario(
                saldo_insoluto=_to_decimal(_first(row, "saldoInsoluto", "deudaPendiente", "saldo")),
                proxima_cuota_clp=_to_decimal(_first(row, "valorCuota", "proximaCuota", "montoCuota")),
                fecha_proximo_pago=_to_date(_first(row, "fechaProximoPago", "fechaVencimiento")),
            )

        # situación: solo registrar deuda vigente (evita filas sin datos)
        situacion = str(_first(esc, "SituacionDeuda", default="")).upper()
        divisa = str(_first(esc, "Divisa", "DIVISA", default="CLP")).upper()
        saldo = _monto_escalar(_first(esc, "MontoCapVigente"))
        cuota_clp = _monto_escalar(_first(esc, "ValCuotaVigML"))
        cuota_uf = _monto_escalar(_first(esc, "ValCuotaVigMO"))
        tasa = _monto_escalar(_first(esc, "TasaInteres"), escala=1_000_000) or None

        def _int(v):
            try:
                return int(re.sub(r"\D", "", str(v))) if v else None
            except (TypeError, ValueError):
                return None

        es_uf = "UF" in divisa
        return SaldoHipotecario(
            nombre=f"Crédito Hipotecario{' UF' if es_uf else ''}",
            saldo_insoluto=Decimal("0") if es_uf else saldo,
            saldo_insoluto_uf=saldo if es_uf else None,
            moneda="UF" if es_uf else "CLP",
            proxima_cuota_clp=cuota_clp,
            proxima_cuota_uf=cuota_uf or None,
            fecha_proximo_pago=None,
            tasa=tasa,
            total_cuotas=_int(_first(esc, "TotalCuotas")),
            cuotas_pagadas=_int(_first(esc, "CuotasPagadas", "UltimCuotaPagada")),
        )

    # Líneas de la cartola que NO son transacciones reales (marcadores de saldo).
    _NO_MOVIMIENTO = {"SALDO INICIAL", "SALDO ANTERIOR", "SALDO FACTURADO", "SALDO FINAL"}

    def _parse_movimientos(self, data: Any, *, estado: str,
                           periodo: Optional[date] = None) -> list[MovimientoNormalizado]:
        """
        Estructura real Santander (MatrizMovimientos):
          Fecha (dd/mm/yyyy), Descripcion (tipo), Comercio (glosa útil), Importe (formato CL),
          IndicadorDebeHaber ('D'=cargo / 'H'=abono), DescripcionRubro, TipoBen.
        """
        movs: list[MovimientoNormalizado] = []
        for row in _find_rows(data):
            tipo = str(_first(row, "Descripcion", "descripcion", "glosa", "detalle", default="")).strip()
            if tipo.upper() in self._NO_MOVIMIENTO:
                continue  # marcador de saldo, no es un movimiento
            comercio = str(_first(row, "Comercio", "comercio", default="")).strip()
            desc = comercio or tipo
            fecha = _to_date(_first(row, "Fecha", "fecha", "fechaTransaccion", "fechaCompra", "fechaMovimiento"))
            if not fecha and not desc:
                continue

            importe = _to_decimal(_first(row, "Importe", "monto", "montoCargo", "cargo", "importe"))
            indicador = str(_first(row, "IndicadorDebeHaber", "indicadorDebeHaber", default="")).strip().upper()
            if indicador == "H":              # haber = abono (pago, devolución) -> negativo
                monto = -abs(importe)
            elif indicador == "D":            # debe = cargo (compra) -> positivo
                monto = abs(importe)
            else:                              # fallback al esquema cargo/abono explícito
                cargo = _to_decimal(_first(row, "montoCargo", "cargo"))
                abono = _to_decimal(_first(row, "montoAbono", "abono"))
                monto = cargo if cargo else (-abono if abono else importe)

            texto = f"{tipo} {comercio}"
            moneda_raw = str(_first(row, "moneda", "divisa", default="")).upper()
            es_usd = (
                "US" in moneda_raw or "DOLAR" in moneda_raw or "DÓLAR" in moneda_raw
                or "MONEDA EXTRANJERA" in texto.upper()
            )
            moneda = "USD" if es_usd else "CLP"

            ca, ct = _detect_cuotas(texto)
            if not ct:
                ca = _first(row, "cuotaActual", "nroCuota")
                ct = _first(row, "cuotaTotal", "totalCuotas")
                ca = int(ca) if ca else None
                ct = int(ct) if ct else None

            movs.append(
                MovimientoNormalizado(
                    fecha=fecha or (periodo or date.today()),
                    descripcion=desc,
                    monto=monto,
                    moneda=moneda,
                    estado=estado,
                    tarjeta_id=re.sub(r"\D", "", str(_first(row, "numeroTarjeta", "tarjeta", default="")))[-4:] or None,
                    cuota_actual=ca,
                    cuota_total=ct,
                    periodo_factura=periodo,
                    tipo_banco=tipo,
                )
            )
        return movs

    def _parse_estado_cuenta(self, data: Any, periodo: Optional[date]) -> Optional[EstadoCuenta]:
        if not isinstance(data, (dict, list)):
            return None
        header = data if isinstance(data, dict) else {}
        per = periodo or _to_date(_first(header, "fechaFacturacion", "periodo")) or date.today()
        tid = re.sub(r"\D", "", str(_first(header, "numeroTarjeta", "tarjeta", default="")))[-4:]
        movimientos = self._parse_movimientos(data, estado="FACTURADO", periodo=per)
        return EstadoCuenta(
            periodo=per,
            tarjeta_id=tid,
            movimientos=movimientos,
            total_facturado_clp=_to_decimal(_first(header, "montoTotalFacturado", "totalFacturado", "montoFacturado")),
            total_facturado_usd=_to_decimal(_first(header, "totalFacturadoDolar")) or None,
            pago_minimo_clp=_to_decimal(_first(header, "pagoMinimo", "montoPagoMinimo")) or None,
            fecha_pago=_to_date(_first(header, "fechaPago", "pagarHasta")),
            tipo_cambio_facturacion=_to_decimal(_first(header, "tipoCambioFacturacion", "tipoCambio")) or None,
        )
