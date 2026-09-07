"""
REI - USB Mode Manager (core/usb_modes.py)
Manages USB controller profiles:
- MODO_NORMAL: Standard USB Host / Storage mode (dwc2,dr_mode=host).
- MODO_TECLADO_HID: USB Gadget HID Keyboard (dwc2,dr_mode=peripheral, libcomposite).
Supports safe simulation in development (REI_DRY_RUN=1).
"""

from enum import Enum
import logging
import os
import subprocess
from typing import Optional, Tuple

logger = logging.getLogger("REI.Core.USBModes")


class USBMode(Enum):
    """Supported USB controller operational profiles."""
    NORMAL = "MODO_NORMAL"
    HID_LINUX = "MODO_TECLADO_HID_LINUX"
    HID_WINDOWS = "MODO_TECLADO_HID_WIN"
    # Backward-compatible alias for existing references
    HID_KEYBOARD = "MODO_TECLADO_HID_LINUX"


class USBModeManager:
    """
    Manages switching and persistence of USB operating modes between
    pure Host mode and USB Gadget (HID Keyboard / libcomposite)
    tailored specifically for Linux and Windows endpoints.
    """

    def __init__(
        self,
        gadget_script_path: str = "/usr/local/bin/usb_gadget.sh",
        service_path: str = "/etc/systemd/system/usb_gadget.service",
        dry_run: Optional[bool] = None,
    ):
        self.gadget_script_path = gadget_script_path
        self.service_path = service_path
        self.dry_run = (
            dry_run
            if dry_run is not None
            else (os.environ.get("REI_DRY_RUN", "0") == "1")
        )
        self._mock_mode: USBMode = USBMode.NORMAL

    def _find_boot_config(self) -> str:
        """Locates active boot configuration file on Raspberry Pi OS."""
        if os.path.exists("/boot/firmware/config.txt"):
            return "/boot/firmware/config.txt"
        return "/boot/config.txt"

    def get_current_mode(self) -> USBMode:
        """Determines active USB profile from system state, config.txt and script tag."""
        if self.dry_run:
            return self._mock_mode

        # Check config.txt dr_mode
        cfg = self._find_boot_config()
        is_peripheral = False
        if os.path.exists(cfg):
            try:
                with open(cfg, "r") as f:
                    content = f.read()
                    if "dr_mode=host" in content:
                        return USBMode.NORMAL
                    elif "dr_mode=peripheral" in content:
                        is_peripheral = True
            except Exception:
                pass

        if not is_peripheral and not os.path.exists("/dev/hidg0"):
            return USBMode.NORMAL

        # If in peripheral / gadget mode, inspect gadget script to distinguish Linux vs Windows
        if os.path.exists(self.gadget_script_path):
            try:
                with open(self.gadget_script_path, "r") as f:
                    script_content = f.read()
                    if "REI_MODE=MODO_TECLADO_HID_WIN" in script_content:
                        return USBMode.HID_WINDOWS
                    elif "REI_MODE=MODO_TECLADO_HID_LINUX" in script_content:
                        return USBMode.HID_LINUX
            except Exception:
                pass

        return USBMode.HID_LINUX

    @staticmethod
    def get_network_manager_config() -> str:
        """Returns NetworkManager config to keep gadget interfaces unmanaged."""
        return (
            "[keyfile]\n"
            "unmanaged-devices=interface-name:usb*;interface-name:rndis*;interface-name:ecm*\n"
        )

    @staticmethod
    def get_dnsmasq_config(ip: str = "10.0.0.1", dhcp_start: str = "10.0.0.2", dhcp_end: str = "10.0.0.10") -> str:
        """Returns isolated dnsmasq configuration for usb0."""
        return (
            "port=0\n"
            "interface=usb0\n"
            "bind-dynamic\n"
            "dhcp-authoritative\n"
            "except-interface=wlan0\n"
            "except-interface=eth0\n"
            f"dhcp-range={dhcp_start},{dhcp_end},255.255.255.0,12h\n"
            "# CRÍTICO: dhcp-option=3 vacío evita que el host tome a la Raspberry como gateway de Internet\n"
            "dhcp-option=3\n"
            "# CRÍTICO: dhcp-option=6 vacío evita redirigir consultas DNS del host a la Pi\n"
            "dhcp-option=6\n"
        )

    def detect_gadget_subnet(self) -> Tuple[str, str, str, str]:
        """
        Returns (ip, netmask, dhcp_start, dhcp_end).
        If wlan0 belongs to 10.0.0.0/24, switches dynamically to 172.20.0.1/24 to prevent routing collisions.
        """
        if self.dry_run:
            return ("10.0.0.1", "255.255.255.0", "10.0.0.2", "10.0.0.10")

        try:
            res = subprocess.run("ip -4 addr show wlan0 2>/dev/null", shell=True, capture_output=True, text=True)
            if "inet 10.0.0." in res.stdout:
                logger.warning("Subnet collision detected with wlan0 (10.0.0.0/24). Using 172.20.0.1/24 for usb0.")
                return ("172.20.0.1", "255.255.255.0", "172.20.0.2", "172.20.0.10")
        except Exception as ex:
            logger.debug(f"Subnet check exception: {ex}")

        return ("10.0.0.1", "255.255.255.0", "10.0.0.2", "10.0.0.10")

    def setup_network_isolation(self) -> bool:
        """
        Prevents NetworkManager and dhcpcd from taking over usb0 or modifying the default gateway on wlan0.
        Configures isolated dnsmasq DHCP/DNS server for usb0 clients.
        """
        if self.dry_run:
            logger.info("[DRY-RUN] Network isolation configured (Simulated).")
            return True

        try:
            # 1. NetworkManager configuration
            if os.path.exists("/etc/NetworkManager/NetworkManager.conf") or os.path.exists("/etc/NetworkManager/conf.d"):
                nm_conf_dir = "/etc/NetworkManager/conf.d"
                subprocess.run(f"sudo mkdir -p {nm_conf_dir}", shell=True, check=False)
                nm_file = f"{nm_conf_dir}/99-rei-usb.conf"
                nm_content = self.get_network_manager_config()
                subprocess.run(f"sudo sh -c 'cat > {nm_file} << \"EOF\"\n{nm_content}EOF'", shell=True, check=False)
                subprocess.run("sudo systemctl reload NetworkManager 2>/dev/null || sudo nmcli general reload 2>/dev/null || true", shell=True, check=False)
                logger.info("NetworkManager unmanaged rule configured for usb interfaces.")

            # 2. dhcpcd configuration
            if os.path.exists("/etc/dhcpcd.conf"):
                subprocess.run(
                    "sudo sh -c 'grep -q \"denyinterfaces usb\\* rndis\\* ecm\\*\" /etc/dhcpcd.conf || "
                    "echo \"denyinterfaces usb* rndis* ecm*\" >> /etc/dhcpcd.conf'",
                    shell=True,
                    check=False,
                )
                logger.info("dhcpcd denyinterfaces rule applied for usb interfaces.")

            # 3. dnsmasq configuration
            ip, _, dhcp_start, dhcp_end = self.detect_gadget_subnet()
            dnsmasq_conf_dir = "/etc/dnsmasq.d"
            subprocess.run(f"sudo mkdir -p {dnsmasq_conf_dir}", shell=True, check=False)
            dnsmasq_file = f"{dnsmasq_conf_dir}/rei-usb.conf"
            dnsmasq_content = self.get_dnsmasq_config(ip=ip, dhcp_start=dhcp_start, dhcp_end=dhcp_end)
            subprocess.run(f"sudo sh -c 'cat > {dnsmasq_file} << \"EOF\"\n{dnsmasq_content}EOF'", shell=True, check=False)

            if os.path.exists("/etc/dnsmasq.conf"):
                subprocess.run(
                    "sudo sed -i 's|^#conf-dir=/etc/dnsmasq.d/,\\*\\.conf|conf-dir=/etc/dnsmasq.d/,*.conf|' /etc/dnsmasq.conf 2>/dev/null || true",
                    shell=True,
                    check=False,
                )

            subprocess.run("sudo systemctl unmask dnsmasq 2>/dev/null || true", shell=True, check=False)
            subprocess.run("sudo systemctl enable dnsmasq 2>/dev/null || true", shell=True, check=False)
            subprocess.run("sudo systemctl restart dnsmasq 2>/dev/null || true", shell=True, check=False)

            logger.info("Isolated dnsmasq configuration written and service enabled for usb0.")
            return True
        except Exception as ex:
            logger.error(f"Failed to setup network isolation: {ex}")
            return False

    @staticmethod
    def generate_gadget_script(target_mode: USBMode, gadget_ip: str = "10.0.0.1") -> str:
        """
        Generates libcomposite bash script tailored specifically for target OS:
        - HID_WINDOWS: RNDIS Network (Interface 0 & 1, linked first with MS OS 1.0 descriptors)
                       + HID Keyboard (/dev/hidg0, Interface 2).
        - HID_LINUX: ECM Network (/dev/usb0) + HID Keyboard (/dev/hidg0).
        """
        if target_mode == USBMode.HID_WINDOWS:
            tag = "MODO_TECLADO_HID_WIN"
            functions_section = """# Habilitar descriptores de sistema operativo de Microsoft (MS OS 1.0)
echo 1 > os_desc/use 2>/dev/null || true
echo 0xcd > os_desc/b_vendor_code 2>/dev/null || true
echo MSFT100 > os_desc/qw_sign 2>/dev/null || true

# 1. Funcion RNDIS / Red Ethernet (Windows) - ENLAZADA PRIMERO (Interfaz 0 y 1)
mkdir -p functions/rndis.usb0 2>/dev/null || true
if [ -d functions/rndis.usb0 ]; then
    echo "02:11:22:33:44:57" > functions/rndis.usb0/host_addr 2>/dev/null || true
    echo "02:11:22:33:44:58" > functions/rndis.usb0/dev_addr 2>/dev/null || true
    mkdir -p functions/rndis.usb0/os_desc/interface.rndis 2>/dev/null || true
    echo RNDIS > functions/rndis.usb0/os_desc/interface.rndis/compatible_id 2>/dev/null || true
    echo 5162001 > functions/rndis.usb0/os_desc/interface.rndis/sub_compatible_id 2>/dev/null || true
    ln -s functions/rndis.usb0 configs/c.1/ 2>/dev/null || true
    ln -s configs/c.1 os_desc 2>/dev/null || ln -s ../configs/c.1 os_desc/c.1 2>/dev/null || true
fi

# 2. Funcion HID Teclado (/dev/hidg0) - ENLAZADA SEGUNDO (Interfaz 2)
mkdir -p functions/hid.usb0
echo 1 > functions/hid.usb0/protocol
echo 1 > functions/hid.usb0/subclass
echo 8 > functions/hid.usb0/report_length
echo "BQEJBqEBBQcZ4CnnFQAlAXUBlQiBApUBdQiBA5UFdQEFCBkBKQWRApUBdQORA5UGdQgVACVlBQcZACllgQDA" | base64 -d > functions/hid.usb0/report_desc
ln -s functions/hid.usb0 configs/c.1/ 2>/dev/null || true"""
        else:
            tag = "MODO_TECLADO_HID_LINUX"
            functions_section = """# 1. Funcion ECM / Ethernet (Linux / macOS)
mkdir -p functions/ecm.usb0 2>/dev/null || true
if [ -d functions/ecm.usb0 ]; then
    echo "02:11:22:33:44:55" > functions/ecm.usb0/host_addr 2>/dev/null || true
    echo "02:11:22:33:44:56" > functions/ecm.usb0/dev_addr 2>/dev/null || true
    ln -s functions/ecm.usb0 configs/c.1/ 2>/dev/null || true
fi

# 2. Funcion HID Teclado (/dev/hidg0)
mkdir -p functions/hid.usb0
echo 1 > functions/hid.usb0/protocol
echo 1 > functions/hid.usb0/subclass
echo 8 > functions/hid.usb0/report_length
echo "BQEJBqEBBQcZ4CnnFQAlAXUBlQiBApUBdQiBA5UFdQEFCBkBKQWRApUBdQORA5UGdQgVACVlBQcZACllgQDA" | base64 -d > functions/hid.usb0/report_desc
ln -s functions/hid.usb0 configs/c.1/ 2>/dev/null || true"""

        return f"""#!/bin/bash
# REI_MODE={tag}
modprobe libcomposite 2>/dev/null || true
cd /sys/kernel/config/usb_gadget/ 2>/dev/null || exit 0

# Limpieza rigurosa de ConfigFS en caliente
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

# Descriptores USB
echo 0x1d6b > idVendor
echo 0x0104 > idProduct
echo 0x0102 > bcdDevice
echo 0x0200 > bcdUSB

# Declarar clase compuesta IAD (Interface Association Descriptor) para usbccgp
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

{functions_section}

# Enlazar al controlador UDC
UDC_DEV=$(ls /sys/class/udc 2>/dev/null | head -n 1)
if [ -n "$UDC_DEV" ]; then
    echo "$UDC_DEV" > UDC
fi

# Configurar direccion IP en usb0 y reiniciar dnsmasq de forma aislada
GADGET_IP="{gadget_ip}"
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
    ip addr add ${{GADGET_IP}}/24 dev usb0 2>/dev/null || true
    systemctl restart dnsmasq 2>/dev/null || true
fi

# Asegurar permisos de acceso a /dev/hidg0
chmod 666 /dev/hidg0 2>/dev/null || true
"""

    def set_mode(self, target_mode: USBMode) -> Tuple[bool, str]:
        """
        Transitions the system to the requested USB mode.
        Returns (success: bool, status_message: str).
        """
        logger.info(f"Transitioning USB mode to: {target_mode.value}")

        if self.dry_run:
            self._mock_mode = target_mode
            logger.info(f"[DRY-RUN] USB Mode switched to {target_mode.value}")
            return True, f"Modo {target_mode.value} configurado (Simulacion)."

        cfg = self._find_boot_config()
        gadget_script = self.gadget_script_path
        service_path = self.service_path

        try:
            # 1. Ensure systemd service exists
            if not os.path.exists(service_path):
                logger.info("Creating systemd service for usb_gadget...")
                service_content = (
                    "[Unit]\n"
                    "Description=USB HID/Net Gadget Initialization\n"
                    "After=systemd-modules-load.service\n\n"
                    "[Service]\n"
                    "Type=oneshot\n"
                    "ExecStart=/bin/bash /usr/local/bin/usb_gadget.sh\n"
                    "RemainAfterExit=yes\n\n"
                    "[Install]\n"
                    "WantedBy=sysinit.target\n"
                )
                subprocess.run(f"sudo sh -c 'cat > {service_path} << \"EOF\"\n{service_content}EOF'", shell=True, check=False)
                subprocess.run("sudo systemctl daemon-reload", shell=True, check=False)

            # 2. Clean previous dwc2 configuration
            subprocess.run(f"sudo sed -i '/dtoverlay=dwc2/d' {cfg}", shell=True, check=False)

            if target_mode == USBMode.NORMAL:
                # Configure Host mode (Pure Host for external antenna / keyboard)
                subprocess.run(f"sudo sh -c 'echo \"dtoverlay=dwc2,dr_mode=host\" >> {cfg}'", shell=True, check=False)
                subprocess.run("sudo systemctl disable usb_gadget.service", shell=True, stderr=subprocess.DEVNULL, check=False)
                subprocess.run("sudo systemctl stop usb_gadget.service", shell=True, stderr=subprocess.DEVNULL, check=False)

                # Unbind ConfigFS gadgets in runtime if active with rigorous teardown
                subprocess.run(
                    "sudo sh -c 'for d in /sys/kernel/config/usb_gadget/*; do [ -d \"$d\" ] && echo \"\" > \"$d/UDC\" 2>/dev/null; "
                    "rm -f \"$d\"/os_desc/* 2>/dev/null; rm -f \"$d\"/configs/*/* 2>/dev/null; "
                    "rmdir \"$d\"/functions/*/*/* \"$d\"/functions/*/* \"$d\"/functions/* 2>/dev/null; "
                    "rmdir \"$d\"/configs/*/strings/* \"$d\"/configs/* \"$d\"/strings/* \"$d\" 2>/dev/null || true; done'",
                    shell=True,
                    check=False
                )

                logger.info("USB controller configured as Pure Host.")
                return True, "Modo Host (Normal) configurado con exito."

            else:
                # 3. Setup network isolation and dnsmasq before activating gadget
                self.setup_network_isolation()
                gadget_ip, _, _, _ = self.detect_gadget_subnet()

                # Configure Peripheral / Gadget mode
                subprocess.run(f"sudo sh -c 'echo \"dtoverlay=dwc2,dr_mode=peripheral\" >> {cfg}'", shell=True, check=False)
                subprocess.run("sudo systemctl enable usb_gadget.service", shell=True, stderr=subprocess.DEVNULL, check=False)

                # Generate script tailored to target_mode (Windows vs Linux)
                sh_script = self.generate_gadget_script(target_mode=target_mode, gadget_ip=gadget_ip)

                # Write script with root privileges
                subprocess.run(
                    f"sudo sh -c 'cat > {gadget_script} << \"EOF\"\n{sh_script}EOF'",
                    shell=True,
                    check=False
                )
                subprocess.run(f"sudo chmod 755 {gadget_script}", shell=True, check=False)

                mode_label = "Windows" if target_mode == USBMode.HID_WINDOWS else "Linux"

                # Execute gadget script directly in runtime
                res = subprocess.run(f"sudo /bin/bash {gadget_script}", shell=True, capture_output=True, text=True)
                if res.returncode == 0:
                    logger.info(f"USB Composite ({mode_label}) gadget initialized successfully.")
                    return True, f"Modo Teclado HID ({mode_label}) configurado con exito."
                else:
                    logger.warning(f"Gadget execution note: {res.stderr[:60]}")
                    return True, f"Modo Teclado HID ({mode_label}) configurado con exito."

        except Exception as ex:
            logger.exception(f"Error switching USB mode: {ex}")
            return False, f"Fallo: {str(ex)[:35]}"
