"""
Cifrado simétrico de credenciales bancarias con Fernet.

La llave sale de la variable de entorno BANCA_FERNET_KEY (generar con
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
Nunca guardar la llave ni las credenciales en el repo.
"""

from __future__ import annotations

import os

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover
    Fernet = None
    InvalidToken = Exception


def _get_fernet() -> "Fernet":
    if Fernet is None:
        raise RuntimeError("Falta 'cryptography'. Instala con: pip install cryptography")
    key = os.environ.get("BANCA_FERNET_KEY", "")
    if not key:
        raise RuntimeError(
            "Define BANCA_FERNET_KEY en el entorno. "
            "Genera una con: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(texto: str) -> bytes:
    return _get_fernet().encrypt((texto or "").encode("utf-8"))


def decrypt(token: bytes) -> str:
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(bytes(token)).decode("utf-8")
    except InvalidToken as e:  # pragma: no cover
        raise RuntimeError("No se pudo descifrar la credencial (¿llave incorrecta?).") from e
