#!/usr/bin/env bash
#
# deploy_pi.sh — Despliega el árbol de trabajo local a la Raspberry Pi (REI).
#
# La Pi es un Pi Zero 2 W con Raspberry Pi OS 32-bit (armv7l): no puede
# ejecutar el binario de opencode (solo hay builds x64/arm64), así que el
# agente corre en el PC y este script sincroniza y reinicia el servicio allí.
#
# Uso:
#   tools/deploy_pi.sh                 # sincroniza y reinicia rei.service
#   tools/deploy_pi.sh --deps          # además instala requirements.txt
#   tools/deploy_pi.sh --tests         # además ejecuta la suite de tests
#   tools/deploy_pi.sh --no-restart    # solo sincroniza
#   tools/deploy_pi.sh --clean         # borra en la Pi lo no versionado
#   tools/deploy_pi.sh --force         # reinicia aunque fallen los tests
#
# Variables de entorno:
#   REI_PI_HOST  alias SSH de la Pi (por defecto: rei-pi)
#   REI_PI_DIR   ruta remota del proyecto (por defecto: /home/raspi/rei)

set -euo pipefail

HOST="${REI_PI_HOST:-rei-pi}"
REMOTE_DIR="${REI_PI_DIR:-/home/raspi/rei}"
SERVICE="${REI_PI_SERVICE:-rei.service}"

DO_RESTART=1
DO_DEPS=0
DO_TESTS=0
DO_CLEAN=0
FORCE=0

log() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
err() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; }

usage() {
    sed -n '3,19p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --deps)       DO_DEPS=1 ;;
        --tests)      DO_TESTS=1 ;;
        --clean)      DO_CLEAN=1 ;;
        --no-restart) DO_RESTART=0 ;;
        --force)      FORCE=1 ;;
        -h|--help)    usage 0 ;;
        *)            err "Opción desconocida: $1"; usage 1 ;;
    esac
    shift
done

# --- Validación previa -------------------------------------------------------
if [ ! -f main.py ] || [ ! -d core ]; then
    err "Este script debe ejecutarse desde la raíz del repositorio REI."
    exit 1
fi

if ! ssh -o BatchMode=yes "$HOST" true 2>/dev/null; then
    err "Sin acceso SSH sin contraseña a '$HOST'."
    err "Prueba: ssh -v $HOST  (alias en ~/.ssh/config)"
    exit 1
fi

# --- Sincronización ----------------------------------------------------------
# Se usa tar por streaming porque rsync no está instalado en el PC de trabajo.
log "Sincronizando → $HOST:$REMOTE_DIR"
tar -czf - \
    --exclude='.git' \
    --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.pytest_cache' \
    --exclude='.agents' \
    --exclude='.opencode' \
    --exclude='opencode.json' \
    --exclude='skills-lock.json' \
    --exclude='config/settings.json' \
    --exclude='config/settings.local.json' \
    --exclude='.env' \
    --exclude='.env.*' \
    --exclude='data/reports' \
    . | ssh "$HOST" "mkdir -p '$REMOTE_DIR' && tar -xzf - -C '$REMOTE_DIR'"

if [ "$DO_CLEAN" -eq 1 ]; then
    log "Limpiando archivos no versionados en la Pi"
    ssh "$HOST" "git -C '$REMOTE_DIR' clean -xfd -e .venv -e 'config/settings.json' -e 'data/reports'" || \
        err "git clean falló (revisa que $REMOTE_DIR sea un repositorio git)"
fi

# --- Dependencias ------------------------------------------------------------
if [ "$DO_DEPS" -eq 1 ]; then
    log "Instalando requirements.txt en el venv de la Pi"
    ssh "$HOST" "cd '$REMOTE_DIR' && .venv/bin/pip install --quiet -r requirements.txt"
fi

# --- Tests -------------------------------------------------------------------
# En la Pi (Cortex-A53 a 1 GHz) el arranque de uvicorn puede tardar más que el
# `time.sleep(0.5)` de tests/test_endpoints.py, así que el test de /w/ALL es
# intermitente bajo carga. Se reintenta una vez antes de darlo por fallido.
if [ "$DO_TESTS" -eq 1 ]; then
    for attempt in 1 2; do
        log "Ejecutando tests en la Pi (intento $attempt/2)"
        if ssh "$HOST" "cd '$REMOTE_DIR' && .venv/bin/python -m unittest discover -s tests"; then
            TESTS_OK=1
            break
        fi
        if [ "$attempt" -eq 1 ]; then
            err "La suite falló; se reintenta (test_web_server_payload_serving es intermitente en la Pi)."
        fi
    done

    if [ "${TESTS_OK:-0}" -ne 1 ]; then
        if [ "$FORCE" -eq 1 ]; then
            err "Tests fallidos, pero --force está activo: se reinicia igualmente."
        else
            err "Los tests fallaron en la Pi; no se reinicia el servicio."
            err "Usa --force para reiniciar de todos modos."
            exit 1
        fi
    fi
fi

# --- Reinicio del servicio ---------------------------------------------------
if [ "$DO_RESTART" -eq 1 ]; then
    log "Reiniciando $SERVICE"
    ssh "$HOST" "sudo systemctl restart '$SERVICE' && sleep 2 && systemctl is-active '$SERVICE'"

    log "Últimas líneas del journal"
    ssh "$HOST" "sudo journalctl -u '$SERVICE' -n 15 --no-pager"
fi

log "Listo."
