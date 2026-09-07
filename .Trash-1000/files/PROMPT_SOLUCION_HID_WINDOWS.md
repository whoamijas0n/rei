# PROMPT PARA AGENTE LLM: TECLADO HID + RED RNDIS + TELEMETRÍA FUNCIONAL EN WINDOWS

> **Uso:** Copia y pega el contenido de la sección [PROMPT PARA EL AGENTE](#prompt-para-el-agente-copiar-a-continuación) directamente en el LLM en modo agente que esté operando en la Raspberry Pi.

---

## AUDITORÍA: ¿POR QUÉ WINDOWS DECÍA "RED NO IDENTIFICADA Y SIN CONEXIÓN"?

### 1. El Conflicto en `dnsmasq` (Causa Raíz de que no hubiera comunicación)
* **Colisión del Puerto 53 con `systemd-resolved`:** En Raspberry Pi OS (Debian 12 Bookworm), el resolver del sistema (`systemd-resolved` o NetworkManager) ya tiene ocupado el puerto UDP/TCP 53. Al arrancar `dnsmasq` sin la directiva `port=0`, intentaba escuchar en el puerto 53 y fallaba (`failed to create listening socket for port 53: Address already in use`).
* **Sintaxis de `dhcp-range` con `usb0`:** La directiva previa `dhcp-range=usb0,10.0.0.2,...` era interpretada por `dnsmasq` como un filtro de etiquetas (*tag* `usb0`), ignorando las solicitudes DHCP Discover entrantes del host Windows.
* **Consecuencia en Windows:** Al no responder el servidor DHCP de la Raspberry, Windows entraba en tiempo de espera y se autoasignaba una IP privada **APIPA (`169.254.x.x`)**. Con una IP `169.254.x.x`, el host Windows no podía alcanzar `http://10.0.0.1:8000`, provocando que la telemetría fallara.

### 2. Aclaración sobre el Mensaje de Windows: "Red no identificada y sin conexión"
* En Windows, el Asistente de Conectividad de Red (NCSI / NLA) etiqueta **cualquier adaptador sin puerta de enlace predeterminada a Internet** como *"Red no identificada"* y *"Sin conexión a Internet"*.
* **ESTO ES NORMAL, ESPERADO Y DESEADO.** Ocurre exactamente igual con adaptadores *Host-Only* de VirtualBox o VMware. Dejar la opción `dhcp-option=3` vacía es lo que garantiza que tu PC con Windows **NO intente enviar su tráfico de Internet a través de la Raspberry Pi**, preservando intacto el Wi-Fi o Ethernet de tu PC.
* La diferencia fundamental es que con DHCP funcional, Windows recibe la IP `10.0.0.2` y la subred `10.0.0.0/24`, por lo que **la comunicación HTTP a `http://10.0.0.1:8000` funciona de inmediato y al 100%**, aunque la interfaz muestre esa etiqueta en el panel de Windows.

### 3. Compatibilidad de PowerShell sin Motor de Internet Explorer
* En Windows 10 y 11 (PowerShell 5.1), comandos como `Invoke-RestMethod` o `irm` pueden fallar con el error `The response content cannot be parsed because the Internet Explorer engine is not available...` si el usuario nunca ha configurado IE.
* Se agregó el parámetro `-useb` (`-UseBasicParsing`) tanto en el micro-stager de inyección HID como en el script de telemetría para garantizar ejecución transparente e instantánea.

---

# PROMPT PARA EL AGENTE (Copiar a continuación)

```markdown
Eres un agente autónomo de ingeniería de sistemas en Raspberry Pi OS. Tu objetivo es configurar el Gadget USB compuesto (Tarjeta de red RNDIS en Interfaz 0/1 + Teclado HID en Interfaz 2) y el servidor DHCP (dnsmasq en modo sólo-DHCP) para que la Raspberry Pi Zero cumpla con los siguientes 3 requerimientos:

1. Windows 10/11 debe reconocer de inmediato la tarjeta de red RNDIS y recibir la IP 10.0.0.2 vía DHCP, permitiendo descargar el payload y enviar la telemetría a http://10.0.0.1:8000.
2. Windows debe mantener el Teclado HID (/dev/hidg0) 100% operativo.
3. La Raspberry Pi debe mantener su conexión a Internet por Wi-Fi (wlan0) sin colisiones de DNS ni de puerta de enlace.

Aplica de forma secuencial y estricta los siguientes pasos:

---

### PASO 1: Configurar dnsmasq en modo Sólo-DHCP (port=0, bind-dynamic)
Configura dnsmasq para servir exclusivamente DHCP en usb0 sin abrir el puerto 53 (evitando choques con systemd-resolved):

```bash
sudo mkdir -p /etc/dnsmasq.d
sudo tee /etc/dnsmasq.d/rei-usb.conf > /dev/null << 'EOF'
port=0
interface=usb0
bind-dynamic
dhcp-authoritative
except-interface=wlan0
except-interface=eth0
dhcp-range=10.0.0.2,10.0.0.10,255.255.255.0,12h
# CRÍTICO: dhcp-option=3 vacío evita que el host tome a la Raspberry como gateway de Internet
dhcp-option=3
# CRÍTICO: dhcp-option=6 vacío evita redirigir consultas DNS del host a la Pi
dhcp-option=6
EOF

if [ -f "/etc/dnsmasq.conf" ]; then
    sudo sed -i 's|^#conf-dir=/etc/dnsmasq.d/,\*\.conf|conf-dir=/etc/dnsmasq.d/,*.conf|' /etc/dnsmasq.conf 2>/dev/null || true
fi

sudo systemctl unmask dnsmasq 2>/dev/null || true
sudo systemctl enable dnsmasq 2>/dev/null || true
sudo systemctl restart dnsmasq 2>/dev/null || true
```

---

### PASO 2: Escribir el script del Gadget USB (/usr/local/bin/usb_gadget.sh)
Escribe el script ConfigFS enlazando primero RNDIS (con descriptores Microsoft OS 1.0) y después el Teclado HID:

```bash
sudo tee /usr/local/bin/usb_gadget.sh > /dev/null << 'EOF'
#!/bin/bash
# REI_MODE=MODO_TECLADO_HID_WIN
modprobe libcomposite 2>/dev/null || true
cd /sys/kernel/config/usb_gadget/ 2>/dev/null || exit 0

# 1. Limpieza rigurosa de ConfigFS en caliente
for dir in /sys/kernel/config/usb_gadget/*; do
    if [ -d "$dir" ]; then
        echo "" > "$dir/UDC" 2>/dev/null || true
        sleep 0.1
        rm -f "$dir"/os_desc/* 2>/dev/null || true
        rm -f "$dir"/configs/*/* 2>/dev/null || true
        rmdir "$dir"/functions/*/*/* 2>/dev/null || true
        rmdir "$dir"/functions/*/* 2>/dev/null || true
        rmdir "$dir"/functions/* 2>/dev/null || true
        rmdir "$dir"/configs/*/strings/* 2>/dev/null || true
        rmdir "$dir"/configs/* 2>/dev/null || true
        rmdir "$dir"/strings/* 2>/dev/null || true
        rmdir "$dir" 2>/dev/null || true
    fi
done

if [ -d rei ]; then
    echo "" > rei/UDC 2>/dev/null || true
    sleep 0.1
    rm -f rei/os_desc/* 2>/dev/null || true
    rm -f rei/configs/*/* 2>/dev/null || true
    rmdir rei/functions/*/*/* 2>/dev/null || true
    rmdir rei/functions/*/* 2>/dev/null || true
    rmdir rei/functions/* 2>/dev/null || true
    rmdir rei/configs/*/strings/* 2>/dev/null || true
    rmdir rei/configs/* 2>/dev/null || true
    rmdir rei/strings/* 2>/dev/null || true
    rmdir rei 2>/dev/null || true
fi

mkdir -p rei
cd rei || exit 1

# 2. Descriptores del dispositivo
echo 0x1d6b > idVendor
echo 0x0104 > idProduct
echo 0x0102 > bcdDevice
echo 0x0200 > bcdUSB

# Clase compuesta IAD para usbccgp.sys en Windows
echo 0xEF > bDeviceClass
echo 0x02 > bDeviceSubClass
echo 0x01 > bDeviceProtocol

mkdir -p strings/0x409
echo "fedcba9876543210" > strings/0x409/serialnumber
echo "REI" > strings/0x409/manufacturer
echo "REI Diagnostic Hub" > strings/0x409/product

mkdir -p configs/c.1/strings/0x409
echo "Config 1" > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower

# 3. Descriptores de Microsoft OS 1.0
echo 1 > os_desc/use 2>/dev/null || true
echo 0xcd > os_desc/b_vendor_code 2>/dev/null || true
echo MSFT100 > os_desc/qw_sign 2>/dev/null || true

# 4. FUNCIÓN RNDIS (Red Windows) - ENLAZADA PRIMERO (Interfaz 0 y 1)
mkdir -p functions/rndis.usb0 2>/dev/null || true
echo "02:11:22:33:44:57" > functions/rndis.usb0/host_addr 2>/dev/null || true
echo "02:11:22:33:44:58" > functions/rndis.usb0/dev_addr 2>/dev/null || true
mkdir -p functions/rndis.usb0/os_desc/interface.rndis 2>/dev/null || true
echo RNDIS > functions/rndis.usb0/os_desc/interface.rndis/compatible_id 2>/dev/null || true
echo 5162001 > functions/rndis.usb0/os_desc/interface.rndis/sub_compatible_id 2>/dev/null || true
ln -s functions/rndis.usb0 configs/c.1/ 2>/dev/null || true
ln -s configs/c.1 os_desc 2>/dev/null || ln -s ../configs/c.1 os_desc/c.1 2>/dev/null || true

# 5. FUNCIÓN TECLADO HID (/dev/hidg0) - ENLAZADA SEGUNDO (Interfaz 2)
mkdir -p functions/hid.usb0
echo 1 > functions/hid.usb0/protocol
echo 1 > functions/hid.usb0/subclass
echo 8 > functions/hid.usb0/report_length
echo "BQEJBqEBBQcZ4CnnFQAlAXUBlQiBApUBdQiBA5UFdQEFCBkBKQWRApUBdQORA5UGdQgVACVlBQcZACllgQDA" | base64 -d > functions/hid.usb0/report_desc
ln -s functions/hid.usb0 configs/c.1/ 2>/dev/null || true

# 6. Vincular al controlador UDC
UDC_DEV=$(ls /sys/class/udc 2>/dev/null | head -n 1)
if [ -n "$UDC_DEV" ]; then
    echo "$UDC_DEV" > UDC
fi

# 7. Asignar IP 10.0.0.1 a usb0 y reiniciar dnsmasq
GADGET_IP="10.0.0.1"
if ip -4 addr show wlan0 2>/dev/null | grep -q "inet 10.0.0."; then
    GADGET_IP="172.20.0.1"
fi

for i in $(seq 1 6); do
    if ip link show usb0 >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

if ip link show usb0 >/dev/null 2>&1; then
    ip link set usb0 up 2>/dev/null || true
    ip addr flush dev usb0 2>/dev/null || true
    ip addr add ${GADGET_IP}/24 dev usb0 2>/dev/null || true
    systemctl restart dnsmasq 2>/dev/null || true
fi

chmod 666 /dev/hidg0 2>/dev/null || true
EOF

sudo chmod 755 /usr/local/bin/usb_gadget.sh
```

---

### PASO 3: Asegurar dtoverlay dwc2 y activar el Gadget
Asegura que el modo periférico esté en el archivo de arranque y recarga el gadget en caliente:

```bash
BOOT_CFG="/boot/firmware/config.txt"
[ ! -f "$BOOT_CFG" ] && BOOT_CFG="/boot/config.txt"

sudo sed -i '/dtoverlay=dwc2/d' "$BOOT_CFG"
sudo sh -c "echo 'dtoverlay=dwc2,dr_mode=peripheral' >> $BOOT_CFG"

sudo systemctl enable usb_gadget.service 2>/dev/null || true
sudo /bin/bash /usr/local/bin/usb_gadget.sh
```

---

### PASO 4: Comprobación y Verificación Local en la Raspberry Pi
Ejecuta las siguientes comprobaciones para certificar el estado:
```bash
# 1. Verificar existencia del dispositivo de teclado
ls -la /dev/hidg0

# 2. Verificar que la red usb0 tiene su IP asignada
ip -4 addr show usb0

# 3. Verificar que la Raspberry Pi mantiene conexión a Internet por wlan0
ping -c 3 8.8.8.8

# 4. Verificar estado activo de dnsmasq (debe estar active running sin errores de puerto 53)
sudo systemctl status dnsmasq --no-pager
```
```

---

## GUÍA DE VERIFICACIÓN EN WINDOWS TRAS APLICAR EL CAMBIO

1. **Reconectar el cable USB de datos** a la PC con Windows (o reiniciar el adaptador de red en Windows).
2. Abre una ventana de **PowerShell** en Windows y ejecuta:
   ```powershell
   # 1. Verificar que Windows recibió la IP 10.0.0.2 en el adaptador USB RNDIS
   Get-NetIPAddress -InterfaceAlias "*Ethernet*" | Select-Object IPAddress, InterfaceAlias, AddressFamily

   # 2. Comprobar conectividad directa al puerto del servidor web en la Raspberry Pi
   Test-NetConnection -ComputerName 10.0.0.1 -Port 8000

   # 3. Probar descarga manual del endpoint de salud (con BasicParsing)
   irm -useb http://10.0.0.1:8000/health
   ```
   * *Resultado esperado en el comando 1:* Una línea mostrando `IPAddress: 10.0.0.2`.
   * *Resultado esperado en el comando 2:* `TcpTestSucceeded : True`.
   * *Resultado esperado en el comando 3:* `{"status":"ok","service":"REI Diagnostic Hub",...}`.
3. En el Administrador de Dispositivos (`devmgmt.msc`):
   * **Adaptadores de red:** Aparecerá **USB Ethernet/RNDIS Gadget** o **Dispositivo compatible con NDIS remoto**.
   * **Teclados / Dispositivos HID:** Aparecerá **Dispositivo de teclado HID**.
> **Nota sobre el estado en Windows:** Que el icono de red en Windows muestre *"Red no identificada - Sin conexión a Internet"* es completamente normal y correcto para este adaptador. Significa que Windows mantiene todo su tráfico de Internet en tu Wi-Fi principal, mientras la subred `10.0.0.0/24` enruta la telemetría a la Raspberry Pi.
