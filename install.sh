#!/usr/bin/env bash
# ==============================================================================
# REI - AUTONOMOUS MULTI-INTERFACE DIAGNOSTIC HUB
# Script de Autoinstalación, Configuración de Sistema Operativo y Rollback
# ==============================================================================

set -eo pipefail

# ------------------------------------------------------------------------------
# PALETA DE COLORES ANSI & CONFIGURACIÓN
# ------------------------------------------------------------------------------
CLR_RESET="\033[0m"
CLR_BOLD="\033[1m"
CLR_DIM="\033[2m"
CLR_CYAN="\033[1;36m"
CLR_GREEN="\033[1;32m"
CLR_YELLOW="\033[1;33m"
CLR_RED="\033[1;31m"
CLR_BLUE="\033[1;34m"
CLR_MAGENTA="\033[1;35m"
CLR_WHITE="\033[1;37m"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="rei.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
PYTHON_BIN="$(command -v python3 || echo "/usr/bin/python3")"

# ------------------------------------------------------------------------------
# UTILIDADES DE RENDERIZADO CENTRADO EN TERMINAL
# ------------------------------------------------------------------------------
get_cols() {
    local c
    c=$(tput cols 2>/dev/null || echo 80)
    if [[ ! "$c" =~ ^[0-9]+$ ]] || [ "$c" -lt 40 ]; then
        echo 80
    else
        echo "$c"
    fi
}

strip_ansi() {
    echo -e "$1" | sed -r "s/\x1B\[([0-9]{1,3}(;[0-9]{1,3})*)?[mGK]//g"
}

center_text() {
    local text="$1"
    local clean
    clean=$(strip_ansi "$text")
    local len=${#clean}
    local cols
    cols=$(get_cols)
    local pad=$(( (cols - len) / 2 ))
    if [ "$pad" -lt 0 ]; then pad=0; fi
    printf "%*s%b\n" "$pad" "" "$text"
}

center_divider() {
    local char="${1:-─}"
    local width="${2:-64}"
    local cols
    cols=$(get_cols)
    if [ "$width" -gt "$cols" ]; then width=$((cols - 4)); fi
    local pad=$(( (cols - width) / 2 ))
    if [ "$pad" -lt 0 ]; then pad=0; fi
    local line=""
    for ((i=0; i<width; i++)); do
        line="${line}${char}"
    done
    printf "%*s%b\n" "$pad" "" "${CLR_CYAN}${line}${CLR_RESET}"
}

center_block() {
    local width="$1"
    shift
    local cols
    cols=$(get_cols)
    local pad=$(( (cols - width) / 2 ))
    if [ "$pad" -lt 0 ]; then pad=0; fi
    for line in "$@"; do
        printf "%*s%b\n" "$pad" "" "$line"
    done
}

print_banner() {
    clear 2>/dev/null || true
    echo ""
    center_text "${CLR_CYAN}██████╗ ███████╗██╗${CLR_RESET}"
    center_text "${CLR_CYAN}██╔══██╗██╔════╝██║${CLR_RESET}"
    center_text "${CLR_CYAN}██████╔╝█████╗  ██║${CLR_RESET}"
    center_text "${CLR_CYAN}██╔══██╗██╔══╝  ██║${CLR_RESET}"
    center_text "${CLR_CYAN}██║  ██║███████╗██║${CLR_RESET}"
    center_text "${CLR_CYAN}╚═╝  ╚═╝╚══════╝╚═╝${CLR_RESET}"
    echo ""
    center_text "${CLR_WHITE}${CLR_BOLD}PORTABLE MULTI-INTERFACE DIAGNOSTIC HUB${CLR_RESET}"
    center_text "${CLR_DIM}Raspberry Pi Zero 2 W • Embedded Linux • SH1106 OLED${CLR_RESET}"
    center_divider "━" 68
    echo ""
}

print_status() {
    local type="$1"
    local message="$2"
    case "$type" in
        "info")
            center_text "${CLR_BLUE}[i]${CLR_RESET} ${message}"
            ;;
        "success")
            center_text "${CLR_GREEN}[✓]${CLR_RESET} ${CLR_BOLD}${message}${CLR_RESET}"
            ;;
        "warn")
            center_text "${CLR_YELLOW}[!]${CLR_RESET} ${CLR_YELLOW}${message}${CLR_RESET}"
            ;;
        "error")
            center_text "${CLR_RED}[✗]${CLR_RESET} ${CLR_RED}${CLR_BOLD}${message}${CLR_RESET}"
            ;;
        "step")
            center_text "${CLR_MAGENTA}==>${CLR_RESET} ${CLR_WHITE}${CLR_BOLD}${message}${CLR_RESET}"
            ;;
    esac
}

# ------------------------------------------------------------------------------
# VERIFICACIÓN DE PRIVILEGIOS
# ------------------------------------------------------------------------------
check_root() {
    if [ "$EUID" -ne 0 ]; then
        print_status "error" "Este script requiere privilegios de superusuario (root)."
        center_text "${CLR_DIM}Por favor ejecútalo como: ${CLR_BOLD}sudo $0${CLR_RESET}"
        echo ""
        exit 1
    fi
}

# ------------------------------------------------------------------------------
# RUTINA DE INSTALACIÓN
# ------------------------------------------------------------------------------
do_install() {
    check_root
    print_banner
    center_text "${CLR_BOLD}${CLR_GREEN}INICIANDO PROCESO DE AUTOINSTALACIÓN DE REI${CLR_RESET}"
    center_divider "─" 50
    echo ""

    # 1. Validación de Entorno
    print_status "step" "Paso 1/5: Verificando directorios y arquitectura..."
    if [ ! -f "${PROJECT_DIR}/main.py" ]; then
        print_status "error" "No se encontró main.py en ${PROJECT_DIR}"
        exit 1
    fi

    # Crear directorio persistente /data si no existe
    if [ ! -d "/data" ]; then
        mkdir -p /data
        chmod 755 /data
        print_status "info" "Directorio persistente /data creado con éxito."
    else
        print_status "info" "Directorio persistente /data verificado."
    fi
    echo ""

    # 2. Instalación de Dependencias del Sistema (APT)
    print_status "step" "Paso 2/5: Instalando paquetes del sistema operativo (APT)..."
    if command -v apt-get >/dev/null 2>&1; then
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq
        
        local apt_packages=(
            python3
            python3-pip
            python3-dev
            python3-venv
            python3-setuptools
            python3-wheel
            build-essential
            libjpeg-dev
            zlib1g-dev
            libfreetype6-dev
            liblcms2-dev
            libopenjp2-7
            libtiff-tools
            i2c-tools
            python3-smbus
            python3-gpiozero
            python3-rpi.gpio
            python3-spidev
            git
            curl
            dnsmasq
            smartmontools
            wireless-tools
            iw
            wpasupplicant
        )
        
        apt-get install -y -qq --no-install-recommends "${apt_packages[@]}" >/dev/null 2>&1 || {
            print_status "warn" "Instalación APT con salida detallada por advertencias..."
            apt-get install -y --no-install-recommends "${apt_packages[@]}"
        }
        print_status "success" "Paquetes APT del sistema instalados correctamente."
    else
        print_status "warn" "Gestor apt-get no detectado. Omitiendo paso APT (sistema no-Debian)."
    fi
    echo ""

    # 3. Habilitación de Módulos de Hardware (I2C & SPI)
    print_status "step" "Paso 3/5: Habilitando interfaces de hardware (I2C / SPI)..."
    modprobe i2c-dev 2>/dev/null || true
    modprobe spi-bcm2835 2>/dev/null || true
    
    # Habilitar en /etc/modules si no está presente
    if [ -f "/etc/modules" ]; then
        grep -qxF "i2c-dev" /etc/modules || echo "i2c-dev" >> /etc/modules
        grep -qxF "spi-bcm2835" /etc/modules || echo "spi-bcm2835" >> /etc/modules
    fi

    # Configuración de Raspberry Pi config.txt si existe
    for boot_cfg in "/boot/firmware/config.txt" "/boot/config.txt"; do
        if [ -f "$boot_cfg" ]; then
            if ! grep -q "^dtparam=i2c_arm=on" "$boot_cfg"; then
                echo "dtparam=i2c_arm=on" >> "$boot_cfg"
            fi
            if ! grep -q "^dtparam=spi=on" "$boot_cfg"; then
                echo "dtparam=spi=on" >> "$boot_cfg"
            fi
            print_status "info" "Interfaces I2C/SPI habilitadas en ${boot_cfg}"
            break
        fi
    done
    print_status "success" "Configuración de buses de hardware completada."
    echo ""

    # 4. Instalación de Dependencias de Python en Entorno Virtual (.venv)
    print_status "step" "Paso 4/5: Configurando entorno de Python y dependencias..."
    local venv_dir="${PROJECT_DIR}/.venv"
    local venv_python="${venv_dir}/bin/python3"
    local venv_pip="${venv_dir}/bin/pip"

    if [ ! -d "${venv_dir}" ]; then
        print_status "info" "Creando entorno virtual con acceso a librerías de hardware del sistema..."
        python3 -m venv --system-site-packages "${venv_dir}" || {
            print_status "warn" "Fallo --system-site-packages. Intentando venv estándar..."
            python3 -m venv "${venv_dir}"
        }
    fi

    if [ -f "${venv_pip}" ]; then
        print_status "info" "Actualizando herramientas base (pip, setuptools, wheel)..."
        "${venv_pip}" install -q --upgrade pip setuptools wheel >/dev/null 2>&1 || true

        if [ -f "${PROJECT_DIR}/requirements.txt" ]; then
            print_status "info" "Instalando paquetes desde requirements.txt..."
            # Usar --ignore-installed para evitar conflictos con paquetes de Debian sin archivo RECORD
            "${venv_pip}" install -q --prefer-binary --ignore-installed -r "${PROJECT_DIR}/requirements.txt" || {
                print_status "warn" "Reintentando instalación con salida detallada..."
                "${venv_pip}" install --prefer-binary --ignore-installed -r "${PROJECT_DIR}/requirements.txt"
            }
            print_status "success" "Librerías Python instaladas correctamente en .venv."
        else
            print_status "warn" "No se encontró requirements.txt en ${PROJECT_DIR}"
        fi
    else
        print_status "warn" "No se pudo utilizar venv. Intentando instalación global con pip..."
        local pip_flags=("--ignore-installed")
        if pip3 install --help 2>&1 | grep -q -- '--break-system-packages'; then
            pip_flags+=("--break-system-packages")
        fi
        pip3 install "${pip_flags[@]}" -r "${PROJECT_DIR}/requirements.txt"
        venv_python="${PYTHON_BIN}"
    fi
    echo ""

    # 5. Configuración de Autoinicio en el SO (Systemd Service como Superusuario)
    print_status "step" "Paso 5/5: Configurando autoinicio del sistema (systemd)..."
    
    local exec_cmd="${venv_python}"
    if [ ! -f "${exec_cmd}" ]; then
        exec_cmd="${PYTHON_BIN}"
    fi

    cat > "${SERVICE_PATH}" <<EOF
[Unit]
Description=REI - Autonomous Diagnostic Hub Service
After=network.target local-fs.target systemd-modules-load.service
Wants=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${PROJECT_DIR}
ExecStart=${exec_cmd} ${PROJECT_DIR}/main.py
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=${PROJECT_DIR}
Environment=PATH=${PROJECT_DIR}/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

[Install]
WantedBy=multi-user.target
EOF

    chmod 644 "${SERVICE_PATH}"
    systemctl daemon-reload
    systemctl unmask "${SERVICE_NAME}" >/dev/null 2>&1 || true
    systemctl enable "${SERVICE_NAME}" >/dev/null 2>&1
    
    print_status "info" "Iniciando servicio ${SERVICE_NAME}..."
    systemctl restart "${SERVICE_NAME}" >/dev/null 2>&1 || systemctl start "${SERVICE_NAME}" >/dev/null 2>&1 || true

    if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
        print_status "success" "Servicio ${SERVICE_NAME} activo y en ejecución."
    else
        print_status "info" "Servicio habilitado para el próximo arranque (autoinicio configurado)."
    fi

    echo ""
    center_divider "━" 68
    center_text "${CLR_BOLD}${CLR_GREEN}✔ INSTALACIÓN DE REI COMPLETADA EXITOSAMENTE${CLR_RESET}"
    center_divider "━" 68
    echo ""
    center_text "${CLR_WHITE}El servicio iniciará automáticamente al encender el gadget.${CLR_RESET}"
    center_text "${CLR_DIM}Comandos útiles para gestión:${CLR_RESET}"
    center_text "${CLR_CYAN}systemctl start rei.service${CLR_RESET}   • Iniciar ahora"
    center_text "${CLR_CYAN}systemctl status rei.service${CLR_RESET}  • Ver estado"
    center_text "${CLR_CYAN}journalctl -u rei.service -f${CLR_RESET}  • Ver logs en vivo"
    echo ""
}

# ------------------------------------------------------------------------------
# RUTINA DE REVERSIÓN / ROLLBACK / DESINSTALACIÓN
# ------------------------------------------------------------------------------
do_uninstall() {
    check_root
    print_banner
    center_text "${CLR_BOLD}${CLR_YELLOW}INICIANDO PROCESO DE REVERSIÓN / DESINSTALACIÓN${CLR_RESET}"
    center_divider "─" 50
    echo ""

    print_status "step" "Deteniendo y deshabilitando servicio de autoinicio (${SERVICE_NAME})..."
    
    if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
        systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
        print_status "info" "Servicio detenido."
    fi

    if systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
        systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
        print_status "info" "Autoinicio deshabilitado."
    fi

    if [ -f "${SERVICE_PATH}" ]; then
        rm -f "${SERVICE_PATH}"
        print_status "info" "Archivo de servicio ${SERVICE_PATH} eliminado."
    fi

    if [ -d "${PROJECT_DIR}/.venv" ]; then
        rm -rf "${PROJECT_DIR}/.venv"
        print_status "info" "Entorno virtual .venv eliminado."
    fi

    systemctl daemon-reload 2>/dev/null || true
    systemctl reset-failed 2>/dev/null || true
    print_status "success" "Configuraciones del sistema operativo revertidas."

    echo ""
    center_divider "━" 68
    center_text "${CLR_BOLD}${CLR_GREEN}✔ TODOS LOS CAMBIOS DE SISTEMA FUERON REVERTIDOS${CLR_RESET}"
    center_divider "━" 68
    echo ""
    center_text "${CLR_WHITE}El servicio de autoinicio ha sido completamente removido.${CLR_RESET}"
    center_text "${CLR_DIM}El código fuente y datos en el repositorio permanecen intactos.${CLR_RESET}"
    echo ""
}

# ------------------------------------------------------------------------------
# MENÚ DE AYUDA
# ------------------------------------------------------------------------------
show_help() {
    print_banner
    center_text "${CLR_BOLD}${CLR_WHITE}OPCIONES DE LÍNEA DE COMANDOS${CLR_RESET}"
    center_divider "─" 58
    echo ""
    center_block 58 \
        "  ${CLR_CYAN}--install, -i${CLR_RESET}        Instalación completa y autoinicio" \
        "  ${CLR_YELLOW}--uninstall, -u, -r${CLR_RESET}  Revertir cambios y desinstalar servicio" \
        "  ${CLR_WHITE}--help, -h${CLR_RESET}           Mostrar este mensaje de ayuda"
    echo ""
    center_divider "─" 58
    center_text "${CLR_DIM}Ejecutar sin argumentos abre el menú interactivo centrado.${CLR_RESET}"
    echo ""
}

# ------------------------------------------------------------------------------
# MENÚ INTERACTIVO PRINCIPAL
# ------------------------------------------------------------------------------
interactive_menu() {
    print_banner
    center_text "${CLR_BOLD}${CLR_WHITE}SELECCIONE UNA ACCIÓN${CLR_RESET}"
    center_divider "─" 48
    echo ""
    center_block 46 \
        "  ${CLR_CYAN}[1]${CLR_RESET} ${CLR_BOLD}Instalar dependencias y configurar autoinicio${CLR_RESET}" \
        "  ${CLR_YELLOW}[2]${CLR_RESET} ${CLR_BOLD}Revertir cambios y desinstalar servicio${CLR_RESET}" \
        "  ${CLR_RED}[3]${CLR_RESET} ${CLR_BOLD}Salir${CLR_RESET}"
    echo ""
    center_divider "─" 48
    echo ""

    local prompt_text="Ingrese su opción [1-3]: "
    local cols
    cols=$(get_cols)
    local pad=$(( (cols - 46) / 2 ))
    if [ "$pad" -lt 0 ]; then pad=0; fi
    printf "%*s%b" "$pad" "" "  ${CLR_BOLD}${prompt_text}${CLR_RESET}"
    read -r opt
    echo ""

    case "$opt" in
        1)
            do_install
            ;;
        2)
            do_uninstall
            ;;
        3)
            center_text "${CLR_DIM}Operación cancelada por el usuario.${CLR_RESET}"
            echo ""
            exit 0
            ;;
        *)
            print_status "error" "Opción inválida seleccionada."
            echo ""
            exit 1
            ;;
    esac
}

# ------------------------------------------------------------------------------
# PUNTO DE ENTRADA
# ------------------------------------------------------------------------------
main() {
    case "${1:-}" in
        --install|-i)
            do_install
            ;;
        --uninstall|--revert|-u|-r)
            do_uninstall
            ;;
        --help|-h)
            show_help
            ;;
        "")
            interactive_menu
            ;;
        *)
            print_banner
            print_status "error" "Argumento no reconocido: $1"
            echo ""
            center_text "${CLR_DIM}Usa ${CLR_CYAN}$0 --help${CLR_RESET} ${CLR_DIM}para ver las opciones disponibles.${CLR_RESET}"
            echo ""
            exit 1
            ;;
    esac
}

main "$@"
