from __future__ import annotations

import hashlib
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests


class UnsafeSourceError(ValueError):
    pass


def _validate_host(hostname):
    if not hostname:
        raise UnsafeSourceError("La URL no tiene host")
    try:
        addresses = {ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise UnsafeSourceError("No se pudo resolver el host") from exc
    if any(address.is_private or address.is_loopback or address.is_link_local or address.is_reserved for address in addresses):
        raise UnsafeSourceError("El host apunta a una red privada")


def fetch_evidence(url, session=None):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise UnsafeSourceError("Solo se permiten URLs HTTPS sin credenciales")
    _validate_host(parsed.hostname)
    client = session or requests.Session()
    response = client.get(url, timeout=10, allow_redirects=True, headers={"User-Agent": "FinanzasPersonalesIA/altcoin-research"}, stream=True)
    response.raise_for_status()
    history = getattr(response, "history", [])
    if isinstance(history, (list, tuple)) and len(history) > 3:
        raise UnsafeSourceError("Demasiados redirects")
    final = urlparse(response.url)
    if final.scheme != "https":
        raise UnsafeSourceError("Redirect inseguro")
    _validate_host(final.hostname)
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    if content_type not in {"text/html", "application/pdf", "application/json"}:
        raise UnsafeSourceError("Tipo de contenido no permitido")
    body = bytearray()
    for chunk in response.iter_content(chunk_size=65536):
        body.extend(chunk)
        if len(body) > 2 * 1024 * 1024:
            raise UnsafeSourceError("La evidencia excede 2 MB")
    return {"url": response.url, "content_type": content_type, "body": bytes(body), "content_hash": hashlib.sha256(body).hexdigest()}
