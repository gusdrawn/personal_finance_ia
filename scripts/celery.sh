#!/usr/bin/env bash
#
# Levanta Celery (worker + Beat) para finanzas_project.
#
#   ./scripts/celery.sh              # worker + beat (programador)
#   ./scripts/celery.sh worker       # solo worker (tareas en background, sin programar)
#
# Requisitos: Redis corriendo. El script lo arranca si no responde.
# Nota macOS: usamos --pool=solo para evitar los crashes de fork de Objective-C
# (y porque para un uso personal no necesitamos concurrencia).

set -euo pipefail
cd "$(dirname "$0")/.."

PY=venv/bin
MODE="${1:-worker+beat}"

# 1) Redis (broker)
if ! redis-cli ping >/dev/null 2>&1; then
    echo "→ Redis no responde; iniciándolo..."
    if command -v brew >/dev/null 2>&1; then
        brew services start redis
    else
        redis-server --daemonize yes
    fi
    sleep 1
fi
redis-cli ping >/dev/null 2>&1 && echo "✓ Redis OK" || { echo "✗ Redis no está disponible"; exit 1; }

# 2) Worker (+ Beat si corresponde)
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES  # seguridad extra en macOS
BEAT=""
[ "$MODE" = "worker+beat" ] && BEAT="-B"

echo "→ Iniciando Celery ($MODE)..."
exec "$PY/celery" -A finanzas_project worker $BEAT --pool=solo -l info
