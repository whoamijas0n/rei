#!/usr/bin/env bash
# ==============================================================================
# REI - USB Composite Gadget Configuration Script (ConfigFS)
# Configures a dual-function USB gadget:
# 1. USB HID Keyboard (/dev/hidg0) - for keystroke injection
# 2. USB CDC-ACM Serial (/dev/ttyGS0) - for in-memory telemetry exfiltration
# ==============================================================================

set -e

GADGET_DIR="/sys/kernel/config/usb_gadget/rei"

teardown_gadget() {
    if [ -d "$GADGET_DIR" ]; then
        echo "[i] Desmontando gadget REI USB existente..."
        cd "$GADGET_DIR"
        
        # Desasociar UDC si está enlazado
        if [ -f UDC ] && [ -n "$(cat UDC 2>/dev/null)" ]; then
            echo "" > UDC 2>/dev/null || true
        fi
        
        # Eliminar enlaces de configuración
        rm -f configs/c.1/hid.usb0 2>/dev/null || true
        rm -f configs/c.1/acm.usb0 2>/dev/null || true
        
        # Eliminar directorios de cadenas y configuración
        rmdir configs/c.1/strings/0x409 2>/dev/null || true
        rmdir configs/c.1 2>/dev/null || true
        
        # Eliminar funciones
        rmdir functions/hid.usb0 2>/dev/null || true
        rmdir functions/acm.usb0 2>/dev/null || true
        
        # Eliminar cadenas de dispositivo y gadget
        rmdir strings/0x409 2>/dev/null || true
        cd /
        rmdir "$GADGET_DIR" 2>/dev/null || true
        echo "[✓] Gadget USB desmontado correctamente."
    fi
}

if [ "$1" == "stop" ]; then
    teardown_gadget
    exit 0
fi

# Cargar módulo libcomposite
if ! lsmod | grep -q libcomposite; then
    echo "[i] Cargando módulo de kernel libcomposite..."
    modprobe libcomposite || {
        echo "[!] Error cargando libcomposite. Verifique que el kernel soporte ConfigFS USB Gadget."
        exit 1
    }
fi

# Limpiar cualquier instancia previa
teardown_gadget

echo "[i] Configurando gadget compuesto REI (HID + CDC-ACM)..."
mkdir -p "$GADGET_DIR"
cd "$GADGET_DIR"

# 1. Identificadores de Dispositivo USB
echo 0x1d6b > idVendor   # Linux Foundation
echo 0x0104 > idProduct  # Multifunction Composite Gadget
echo 0x0100 > bcdDevice  # v1.0.0
echo 0x0200 > bcdUSB     # USB 2.0

# 2. Cadenas de Texto de Identificación
mkdir -p strings/0x409
SERIAL_NUM=$(grep Serial /proc/cpuinfo 2>/dev/null | awk '{print $3}' || echo "REI-ZERO2W-001")
echo "$SERIAL_NUM" > strings/0x409/serialnumber
echo "REI Hardware Diagnostics" > strings/0x409/manufacturer
echo "REI Pocket Multi-Tool" > strings/0x409/product

# 3. Función 1: Teclado USB HID (/dev/hidg0)
mkdir -p functions/hid.usb0
echo 1 > functions/hid.usb0/protocol     # Keyboard
echo 1 > functions/hid.usb0/subclass     # Boot Interface
echo 8 > functions/hid.usb0/report_length

# Descriptor de reporte estándar de teclado HID (63 bytes)
echo -ne \
\\x05\\x01\\x09\\x06\\xa1\\x01\\x05\\x07\\x19\\xe0\\x29\\xe7\\x15\\x00\\x25\\x01\
\\x75\\x01\\x95\\x08\\x81\\x02\\x95\\x01\\x75\\x08\\x81\\x01\\x95\\x05\\x75\\x01\
\\x05\\x08\\x19\\x01\\x29\\x05\\x91\\x02\\x95\\x01\\x75\\x03\\x91\\x01\\x95\\x06\
\\x75\\x08\\x15\\x00\\x25\\x65\\x05\\x07\\x19\\x00\\x29\\x65\\x81\\x00\\xc0 \
> functions/hid.usb0/report_desc

# 4. Función 2: Puerto Serie CDC-ACM (/dev/ttyGS0)
mkdir -p functions/acm.usb0

# 5. Configuración USB c.1
mkdir -p configs/c.1/strings/0x409
echo "Config 1: REI HID Keyboard + CDC-ACM Serial" > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower # 500mA máx

# Enlazar funciones a la configuración
ln -s functions/hid.usb0 configs/c.1/
ln -s functions/acm.usb0 configs/c.1/

# 6. Vincular a controlador UDC
UDC_NAME=$(ls /sys/class/udc 2>/dev/null | head -n 1 || true)
if [ -z "$UDC_NAME" ]; then
    echo "[!] Advertencia: No se encontró controlador UDC activo en /sys/class/udc."
    echo "[!] Verifique que 'dtoverlay=dwc2' esté activo en /boot/firmware/config.txt."
else
    echo "$UDC_NAME" > UDC
    echo "[✓] Gadget USB asociado al controlador UDC: $UDC_NAME"
fi

# 7. Permisos para los nodos de dispositivo
sleep 0.5
chmod 666 /dev/hidg0 2>/dev/null || true
chmod 666 /dev/ttyGS0 2>/dev/null || true

echo "[✓] Configuración de Gadget USB completada exitosamente."
