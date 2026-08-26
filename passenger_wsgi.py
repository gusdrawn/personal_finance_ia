"""Wrapper compatible con el archivo que espera Python Selector."""

import importlib.machinery
import importlib.util
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
loader = importlib.machinery.SourceFileLoader(
    'finance_wsgi', str(BASE_DIR / 'finance_wsgi.py')
)
spec = importlib.util.spec_from_file_location('finance_wsgi', loader=loader)
wsgi = importlib.util.module_from_spec(spec)
loader.exec_module(wsgi)
application = wsgi.application
