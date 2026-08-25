"""
Helpers para guardar/leer el `storage_state` de Playwright por banco+usuario,
y así evitar re-login en cada corrida.
"""

from __future__ import annotations

import os
from pathlib import Path

_BASE = Path(os.environ.get("BANK_SYNC_SESSION_DIR", Path.home() / ".bank_sync" / "sessions"))


def default_storage_path(banco: str, identificador: str = "default") -> str:
    """
    Ruta del archivo de sesión para un banco y un identificador (p.ej. RUT o user id).
    Crea el directorio si no existe.
    """
    safe = "".join(c for c in identificador if c.isalnum() or c in "-_.") or "default"
    _BASE.mkdir(parents=True, exist_ok=True)
    return str(_BASE / f"{banco}_{safe}.json")
