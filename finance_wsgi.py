"""WSGI real para Python Selector/Passenger en cPanel."""

import os
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))


def _load_dotenv():
    """Carga secretos de producción sin exponer credenciales de cPanel al proceso."""
    dotenv_path = BASE_DIR / '.env'
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if not key or key.startswith('CPANEL_') or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


_load_dotenv()
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'finanzas_project.settings')

from finanzas_project.wsgi import application  # noqa: E402
