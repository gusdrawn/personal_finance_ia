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

from django.utils import timezone

from banca.models import CategoriaTematica, MapeoComercio, MovimientoBancario

logger = logging.getLogger("banca.clasificacion")

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


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

    def __init__(
        self,
        modelo: str = "anthropic/claude-3.5-haiku",
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
    ):
        self.modelo = modelo
        self.base_url = _normalizar_base_url(base_url)
        self.api_key = api_key or os.environ.get("IA_API_KEY") or os.environ.get("OPENROUTER_API_KEY", "")

    def disponible(self) -> bool:
        return bool(self.api_key)

    def clasificar(self, glosas: list[str], categorias: list[str]) -> list[dict]:
        if not self.disponible() or not glosas:
            return []
        import requests

        sistema = (
            "Eres un clasificador de gastos bancarios chilenos. Para cada glosa, devuelve "
            "la categoría más adecuada de la lista provista; si ninguna calza, propone una "
            "categoría nueva corta. Responde SOLO JSON válido."
        )
        usuario = {
            "categorias_existentes": categorias,
            "glosas": glosas,
            "formato_salida": [{"glosa": "...", "categoria": "...", "confianza": 0.0}],
        }
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.modelo,
                    "messages": [
                        {"role": "system", "content": sistema},
                        {"role": "user", "content": json.dumps(usuario, ensure_ascii=False)},
                    ],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
                timeout=60,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
            filas = data if isinstance(data, list) else data.get("resultados") or data.get("glosas") or []
            return filas
        except Exception as e:  # pragma: no cover
            logger.warning("Clasificador IA falló: %s", e)
            return []


# Alias retrocompatible
OpenRouterClasificador = OpenAICompatibleClasificador


# --------------------------------------------------------------------- servicio
def _categoria(user, nombre: str) -> CategoriaTematica:
    nombre = (nombre or "Sin categoría").strip()[:100]
    cat, _ = CategoriaTematica.objects.get_or_create(user=user, nombre=nombre)
    return cat


def _aplicar(mov: MovimientoBancario, categoria: CategoriaTematica, fuente: str, confianza=None):
    mov.categoria = categoria
    mov.esencialidad = categoria.esencialidad_defecto
    mov.fuente_clasificacion = fuente
    mov.confianza_ia = confianza
    mov.clasificado_at = timezone.now()
    mov.save(update_fields=[
        "categoria", "esencialidad", "fuente_clasificacion", "confianza_ia", "clasificado_at", "updated_at",
    ])


def clasificar_pendientes(user, ids: list[int] | None = None, clasificador: ClasificadorIA | None = None) -> dict:
    """Clasifica movimientos sin categoría. Devuelve conteos por fuente."""
    qs = MovimientoBancario.objects.filter(
        user=user, categoria__isnull=True, categoria_es_manual=False, naturaleza="OPERACIONAL"
    )
    if ids is not None:
        qs = qs.filter(id__in=ids)
    pendientes = list(qs)
    if not pendientes:
        return {"regla": 0, "ia": 0, "sin_resolver": 0}

    mapeos = list(MapeoComercio.objects.filter(user=user))
    por_regla, sin_resolver = 0, []

    # 1. resolver por MapeoComercio
    for mov in pendientes:
        glosa = normalizar_glosa(mov.descripcion)
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
        # mapa glosa_norm -> (categoria, confianza)
        decision = {}
        for fila in resultados:
            g = normalizar_glosa(str(fila.get("glosa", "")))
            cat_nombre = str(fila.get("categoria", "")).strip()
            if g and cat_nombre:
                decision[g] = (cat_nombre, fila.get("confianza"))

        for mov, glosa in sin_resolver:
            if glosa in decision:
                cat_nombre, conf = decision[glosa]
                categoria = _categoria(user, cat_nombre)
                _aplicar(mov, categoria, "IA", conf)
                MapeoComercio.objects.get_or_create(
                    user=user, patron=glosa,
                    defaults={"categoria": categoria, "origen": "IA"},
                )
                por_ia += 1

    return {
        "regla": por_regla,
        "ia": por_ia,
        "sin_resolver": len(sin_resolver) - por_ia,
    }


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
