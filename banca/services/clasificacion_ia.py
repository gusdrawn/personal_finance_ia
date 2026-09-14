"""
Clasificación de movimientos con IA vía un proveedor compatible con OpenAI (ver §14.3).

Funciona con cualquier gateway que hable el formato OpenAI: OpenRouter, OpenCode Zen, etc.
El proveedor (base URL + API key + modelo) se configura por usuario en `ConfiguracionBanca`.

Principio: clasifica una vez, reutiliza siempre.
  1. Lo ya conocido se resuelve por `MapeoComercio` (sin llamar a la IA).
  2. Solo las glosas nuevas se mandan en lote a la IA.
  3. Cada resultado nuevo crea/actualiza un MapeoComercio para no volver a gastar llamadas.
  4. La clasificación manual del usuario nunca se pisa.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

from django.db import transaction
from django.utils import timezone

from banca.models import CategoriaTematica, MapeoComercio, MovimientoBancario

logger = logging.getLogger("banca.clasificacion")

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

REGLAS_BANCARIAS = (
    (
        re.compile(r"\bREMUNERACI(?:ON|ONES)\b|\bSUELDO\b|\bN[ÓO]MINA\b", re.I),
        "Sueldos",
        "INGRESO",
    ),
    (
        re.compile(
            r"\bCOM(?:ISION)?[. ]|\bINTERESES?\b|IMP(?:UESTO)?[/ ]SOBREG|CARGO\s+CTACTE",
            re.I,
        ),
        "Intereses y comisiones",
        "EGRESO",
    ),
)


def normalizar_glosa(texto: str) -> str:
    s = (texto or "").lower()
    s = re.sub(r"\d{2,}", "", s)  # quitar números largos (folios, montos)
    s = re.sub(r"[^a-záéíóúüñ ]", " ", s)
    return " ".join(s.split())[:120]


def _normalizar_base_url(base_url: str) -> str:
    """Quita barras y sufijos /chat/completions para quedarnos con la raíz del API."""
    b = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    for suf in ("/chat/completions", "/chat", "/completions"):
        if b.endswith(suf):
            b = b[: -len(suf)]
    return b


def listar_modelos(base_url: str, api_key: str, *, timeout: int = 30) -> list[str]:
    """Consulta el endpoint /models (compatible OpenAI) y devuelve IDs de modelo ordenados."""
    if not api_key:
        return []
    import requests

    url = f"{_normalizar_base_url(base_url)}/models"
    resp = requests.get(url, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    filas = data.get("data") if isinstance(data, dict) else data
    ids = []
    for f in filas or []:
        mid = f.get("id") if isinstance(f, dict) else str(f)
        if mid:
            ids.append(mid)
    return sorted(set(ids))


# --------------------------------------------------------------------- proveedor IA
class ClasificadorIA:
    """Interfaz intercambiable de clasificación."""

    def clasificar(self, glosas: list[str], categorias: list[str]) -> list[dict]:
        raise NotImplementedError


class OpenAICompatibleClasificador(ClasificadorIA):
    """Clasificador genérico para cualquier gateway compatible con OpenAI."""

    TAMANO_LOTE = 20

    def __init__(
        self,
        modelo: str = "anthropic/claude-3.5-haiku",
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
    ):
        self.modelo = modelo
        self.base_url = _normalizar_base_url(base_url)
        self.api_key = api_key or os.environ.get("IA_API_KEY") or os.environ.get("OPENROUTER_API_KEY", "")
        self.ultimo_error = ""

    def disponible(self) -> bool:
        return bool(self.api_key)

    def clasificar(self, glosas: list[str], categorias: list[str]) -> list[dict]:
        if not self.disponible() or not glosas:
            return []
        import requests

        self.ultimo_error = ""

        sistema = (
            "Eres un clasificador de gastos bancarios chilenos. Para cada glosa, devuelve "
            "la categoría más adecuada de la lista provista; si ninguna calza, propone una "
            "categoría nueva corta. Responde SOLO JSON válido."
        )
        resultados = []
        lotes = [
            (glosas[inicio : inicio + self.TAMANO_LOTE], 0)
            for inicio in range(0, len(glosas), self.TAMANO_LOTE)
        ]
        while lotes:
            lote, intento = lotes.pop(0)
            usuario = {
                "categorias_existentes": categorias,
                "glosas": lote,
                "formato_salida": [
                    {"glosa": "...", "categoria": "...", "confianza": 0.0}
                ],
            }
            try:
                payload = {
                    "model": self.modelo,
                    "messages": [
                        {"role": "system", "content": sistema},
                        {
                            "role": "user",
                            "content": json.dumps(usuario, ensure_ascii=False),
                        },
                    ],
                    "temperature": 0,
                    "max_tokens": 4096,
                    "response_format": {"type": "json_object"},
                }
                if self.modelo.lower().startswith("glm-"):
                    payload["reasoning_effort"] = "low"
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=60,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                data = json.loads(content)
                if isinstance(data, list):
                    filas = data
                elif isinstance(data, dict):
                    filas = next(
                        (
                            data.get(clave)
                            for clave in (
                                "resultados",
                                "glosas",
                                "respuesta",
                                "clasificaciones",
                                "items",
                                "data",
                            )
                            if isinstance(data.get(clave), list)
                        ),
                        [],
                    )
                else:
                    filas = []
                esperadas = {normalizar_glosa(glosa) for glosa in lote}
                recibidas = {
                    normalizar_glosa(str(fila.get("glosa", "")))
                    for fila in filas
                    if isinstance(fila, dict)
                }
                filas_validas = [
                    fila
                    for fila in filas
                    if isinstance(fila, dict)
                    and normalizar_glosa(str(fila.get("glosa", ""))) in esperadas
                ]
                resultados.extend(filas_validas)
                faltantes = [
                    glosa
                    for glosa in lote
                    if normalizar_glosa(glosa) not in recibidas
                ]
                if faltantes:
                    if intento >= 2:
                        self.ultimo_error = (
                            "Proveedor IA: devolvió una respuesta incompleta o con un formato "
                            f"inesperado para {len(faltantes)} movimiento(s) después de reintentar."
                        )
                        logger.warning("Clasificador IA falló: %s", self.ultimo_error)
                        return []
                    siguiente_tamano = 5 if intento == 0 else 1
                    lotes.extend(
                        (faltantes[posicion : posicion + siguiente_tamano], intento + 1)
                        for posicion in range(0, len(faltantes), siguiente_tamano)
                    )
            except Exception as e:
                mensaje = ""
                response = getattr(e, "response", None)
                if response is not None:
                    try:
                        detalle = response.json()
                        error = (
                            detalle.get("error", {})
                            if isinstance(detalle, dict)
                            else {}
                        )
                        mensaje = (
                            error.get("message", "")
                            if isinstance(error, dict)
                            else ""
                        )
                    except Exception:
                        mensaje = ""
                    estado = getattr(response, "status_code", None)
                    prefijo = f"Proveedor IA ({estado})" if estado else "Proveedor IA"
                else:
                    prefijo = "Proveedor IA"
                self.ultimo_error = f"{prefijo}: {mensaje or str(e)}"[:1000]
                logger.warning("Clasificador IA falló: %s", self.ultimo_error)
                return []
        return resultados


# Alias retrocompatible
OpenRouterClasificador = OpenAICompatibleClasificador


# --------------------------------------------------------------------- servicio
def _categoria(user, nombre: str, *, aplica_a: str = "EGRESO") -> CategoriaTematica:
    nombre = (nombre or "Sin categoría").strip()[:100]
    cat, _ = CategoriaTematica.objects.get_or_create(
        user=user, nombre=nombre, defaults={"origen": "IA", "aplica_a": aplica_a}
    )
    return cat


def guardar_mapeo_comercio(
    user, patron: str, categoria: CategoriaTematica, *, origen: str = "MANUAL"
):
    """Guarda la categoría aprendida para un comercio, aislada por usuario.

    ``patron`` puede ser una glosa completa del banco o un nombre corto ingresado por
    la persona. Se normaliza igual que las glosas que llegan desde futuros estados de
    cuenta, por lo que una regla manual y una decisión de IA se aplican con el mismo
    criterio.
    """
    if categoria.user_id != user.id:
        raise ValueError("La categoría debe pertenecer al usuario del mapeo.")
    patron_normalizado = normalizar_glosa(patron)
    if not patron_normalizado:
        return None
    mapeo, _ = MapeoComercio.objects.update_or_create(
        user=user,
        patron=patron_normalizado,
        defaults={"categoria": categoria, "origen": origen},
    )
    return mapeo


def _aplicar(mov: MovimientoBancario, categoria: CategoriaTematica, fuente: str, confianza=None):
    mov.categoria = categoria
    mov.esencialidad = categoria.esencialidad_defecto
    mov.fuente_clasificacion = fuente
    mov.confianza_ia = confianza
    mov.clasificado_at = timezone.now()
    mov.save(update_fields=[
        "categoria", "esencialidad", "fuente_clasificacion", "confianza_ia", "clasificado_at", "updated_at",
    ])


def clasificar_pendientes(
    user,
    ids: list[int] | None = None,
    clasificador: ClasificadorIA | None = None,
    *,
    usar_mapeos_ia: bool = True,
) -> dict:
    """Clasifica movimientos sin categoría. Devuelve conteos por fuente."""
    qs = MovimientoBancario.objects.filter(
        user=user, categoria__isnull=True, categoria_es_manual=False, naturaleza="OPERACIONAL"
    ).exclude(estado="DESCARTADO_CIERRE")
    if ids is not None:
        qs = qs.filter(id__in=ids)
    pendientes = list(qs)
    if not pendientes:
        return {"regla": 0, "ia": 0, "sin_resolver": 0}

    mapeos_qs = MapeoComercio.objects.filter(user=user)
    if not usar_mapeos_ia:
        mapeos_qs = mapeos_qs.filter(origen="MANUAL")
    mapeos = list(mapeos_qs)
    por_regla, sin_resolver = 0, []

    # 0. Conceptos bancarios inequívocos: sueldo y costos de la cuenta no
    # necesitan IA y conservan la dirección que informa la cartola.
    for mov in pendientes:
        glosa = normalizar_glosa(mov.descripcion)
        regla_bancaria = next(
            (
                (nombre, aplica_a)
                for patron, nombre, aplica_a in REGLAS_BANCARIAS
                if patron.search(mov.descripcion or "")
                and aplica_a == ("EGRESO" if mov.es_cargo else "INGRESO")
            ),
            None,
        )
        if regla_bancaria:
            nombre, aplica_a = regla_bancaria
            _aplicar(mov, _categoria(user, nombre, aplica_a=aplica_a), "REGLA")
            por_regla += 1
            continue

        # 1. resolver por MapeoComercio
        match = next((m for m in mapeos if m.patron and m.patron in glosa), None)
        if match:
            _aplicar(mov, match.categoria, "REGLA")
            match.hits += 1
            match.save(update_fields=["hits", "updated_at"])
            por_regla += 1
        else:
            sin_resolver.append((mov, glosa))

    # 2. IA solo para glosas nuevas (deduplicadas)
    por_ia = 0
    if sin_resolver:
        clasificador = clasificador or _clasificador_usuario(user)
        glosas_unicas = sorted({g for _, g in sin_resolver if g})
        categorias_existentes = list(
            CategoriaTematica.objects.filter(user=user, activa=True).values_list("nombre", flat=True)
        )
        resultados = clasificador.clasificar(glosas_unicas, categorias_existentes) if glosas_unicas else []
        error_proveedor = getattr(clasificador, "ultimo_error", "")
        if not isinstance(error_proveedor, str):
            error_proveedor = ""
        # mapa glosa_norm -> (categoria, confianza)
        decision = {}
        for fila in resultados or []:
            if not isinstance(fila, dict):
                continue
            g = normalizar_glosa(str(fila.get("glosa", "")))
            cat_nombre = str(fila.get("categoria", "")).strip()
            if g and cat_nombre:
                decision[g] = (cat_nombre, fila.get("confianza"))

        for mov, glosa in sin_resolver:
            if glosa in decision:
                cat_nombre, conf = decision[glosa]
                categoria = _categoria(user, cat_nombre)
                _aplicar(mov, categoria, "IA", conf)
                guardar_mapeo_comercio(user, glosa, categoria, origen="IA")
                por_ia += 1

    resultado = {
        "regla": por_regla,
        "ia": por_ia,
        "sin_resolver": len(sin_resolver) - por_ia,
    }
    if sin_resolver and error_proveedor:
        resultado["error"] = error_proveedor
    return resultado


def recalcular_clasificaciones(
    user, ids: list[int] | None = None, clasificador: ClasificadorIA | None = None
) -> dict:
    """Recalcula IA/reglas sin tocar ninguna decisión manual del usuario."""
    scope = MovimientoBancario.objects.filter(user=user).exclude(
        estado="DESCARTADO_CIERRE"
    )
    if ids is not None:
        scope = scope.filter(id__in=ids)
    protegidos = scope.filter(
        categoria_es_manual=True
    ).count() + scope.filter(fuente_clasificacion="MANUAL", categoria_es_manual=False).count()
    elegibles = scope.filter(
        categoria_es_manual=False,
        naturaleza="OPERACIONAL",
    ).exclude(fuente_clasificacion="MANUAL")
    elegibles_ids = list(elegibles.values_list("id", flat=True))

    class _CancelarRecalculo(Exception):
        def __init__(self, resultado):
            self.resultado = resultado

    try:
        with transaction.atomic():
            elegibles.update(
                categoria=None,
                confianza_ia=None,
                clasificado_at=None,
                fuente_clasificacion="PENDIENTE",
            )
            resultado = clasificar_pendientes(
                user,
                ids=elegibles_ids,
                clasificador=clasificador,
                usar_mapeos_ia=False,
            )
            if resultado.get("error"):
                raise _CancelarRecalculo(resultado)
    except _CancelarRecalculo as exc:
        resultado = exc.resultado
        resultado["ia"] = 0
        resultado["regla"] = 0
        resultado["sin_resolver"] = scope.filter(
            categoria__isnull=True,
            categoria_es_manual=False,
            naturaleza="OPERACIONAL",
        ).exclude(fuente_clasificacion="MANUAL").count()
        resultado["recalculados"] = 0
        resultado["restaurados"] = len(elegibles_ids)
        resultado["manuales_protegidos"] = protegidos
        return resultado
    resultado["recalculados"] = len(elegibles_ids)
    resultado["restaurados"] = 0
    resultado["manuales_protegidos"] = protegidos
    return resultado


def _clasificador_usuario(user) -> "OpenAICompatibleClasificador":
    """Construye el clasificador a partir de la ConfiguracionBanca del usuario."""
    from banca.models import ConfiguracionBanca

    cfg = ConfiguracionBanca.objects.filter(user=user).first()
    if not cfg:
        return OpenAICompatibleClasificador()
    try:
        api_key = cfg.get_ia_api_key()
    except Exception:  # llave Fernet ausente/incorrecta
        api_key = ""
    return OpenAICompatibleClasificador(
        modelo=cfg.modelo_ia or "anthropic/claude-3.5-haiku",
        api_key=api_key or None,
        base_url=cfg.ia_base_url or DEFAULT_BASE_URL,
    )
