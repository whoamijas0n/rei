"""
REI - USB Mode Manager (core/usb_modes.py)
Manages USB controller profiles:
- MODO_NORMAL: Standard USB Host / Storage mode.
- MODO_TECLADO_HID: Composite USB Gadget (HID Keyboard + RNDIS/ECM Ethernet).
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
    HID_KEYBOARD = "MODO_TECLADO_HID"


# Template for Composite Gadget Script (/usr/local/bin/usb_gadget.sh)
USB_GADGET_SCRIPT_TEMPLATE = """#!/bin/bash
# REI - Composite USB Gadget Initializer (HID Keyboard + RNDIS)
set -e

CONFIGFS_DIR="/sys/kernel/config/usb_gadget/rei"
UDC_DEVICE=$(ls /sys/class/udc | head -n 1)

if [ -z "$UDC_DEVICE" ]; then
    echo "[!] No UDC device found. Is dwc2 enabled in /boot/config.txt?"
    exit 1
fi

if [ -d "$CONFIGFS_DIR" ]; then
    echo "[i] REI gadget already created. Re-binding..."
    echo "" > "$CONFIGFS_DIR/UDC" || true
    echo "$UDC_DEVICE" > "$CONFIGFS_DIR/UDC"
    exit 0
fi

mkdir -p "$CONFIGFS_DIR"
cd "$CONFIGFS_DIR"

# USB Device Descriptors
echo 0x1d6b > idVendor  # Linux Foundation
echo 0x0104 > idProduct # Multifunction Composite Gadget
echo 0x0100 > bcdDevice # v1.0.0
echo 0x0200 > bcdUSB    # USB 2.0

mkdir -p strings/0x409
echo "REI-001" > strings/0x409/serialnumber
echo "REI Project" > strings/0x409/manufacturer
echo "REI Multi-Tool Gadget" > strings/0x409/product

mkdir -p configs/c.1/strings/0x409
echo "Config 1: HID Keyboard + RNDIS" > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower

# Function 1: HID Keyboard
mkdir -p functions/hid.usb0
echo 1 > functions/hid.usb0/protocol
echo 1 > functions/hid.usb0/subclass
echo 8 > functions/hid.usb0/report_length
# Standard USB HID Keyboard Report Descriptor (63 bytes)
echo -ne \\x05\\x01\\x09\\x06\\xa1\\x01\\x05\\x07\\x19\\xe0\\x29\\xe7\\x15\\x00\\x25\\x01\\x75\\x01\\x95\\x08\\x81\\x02\\x95\\x01\\x75\\x08\\x81\\x03\\x95\\x05\\x75\\x01\\x05\\x08\\x19\\x01\\x29\\x05\\x91\\x02\\x95\\x01\\x75\\x03\\x91\\x03\\x95\\x06\\x75\\x08\\x15\\x00\\x25\\x65\\x05\\x07\\x19\\x00\\x29\\x65\\x81\\x00\\xc0 > functions/hid.usb0/report_desc

# Function 2: RNDIS Virtual Ethernet
mkdir -p functions/rndis.usb0 || true

# Link functions to configuration
ln -s functions/hid.usb0 configs/c.1/ || true
if [ -d functions/rndis.usb0 ]; then
    ln -s functions/rndis.usb0 configs/c.1/ || true
fi

# Enable gadget by attaching to UDC
echo "$UDC_DEVICE" > UDC
echo "[✓] REI USB Composite Gadget initialized successfully."
"""

# Systemd Service Template (/etc/systemd/system/usb_gadget.service)
USB_GADGET_SERVICE_TEMPLATE = """[Unit]
Description=REI USB Composite Gadget Service (HID + RNDIS)
After=systemd-modules-load.service local-fs.target
DefaultDependencies=no

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/usb_gadget.sh
ExecStop=/bin/sh -c 'echo "" > /sys/kernel/config/usb_gadget/rei/UDC 2>/dev/null || true'

[Install]
WantedBy=sysinit.target
"""


class USBModeManager:
    """
    Manages switching and persistence of USB operating modes.
    """

    def __init__(
        self,
        gadget_script_path: str = "/usr/local/bin/usb_gadget.sh",
        gadget_service_name: str = "usb_gadget.service",
        dry_run: Optional[bool] = None,
    ):
        self.gadget_script_path = gadget_script_path
        self.gadget_service_name = gadget_service_name
        self.dry_run = (
            dry_run
            if dry_run is not None
            else (os.environ.get("REI_DRY_RUN", "0") == "1")
        )
        self._mock_mode: USBMode = USBMode.NORMAL

    def _find_boot_config(self) -> Optional[str]:
        """Locates active boot configuration file on Raspberry Pi OS."""
        candidates = ["/boot/firmware/config.txt", "/boot/config.txt"]
        for p in candidates:
            if os.path.isfile(p):
                return p
        return None

    def get_current_mode(self) -> USBMode:
        """Determines active USB profile from system state."""
        if self.dry_run:
            return self._mock_mode

        # Check if USB gadget is bound to UDC
        udc_bound_path = "/sys/kernel/config/usb_gadget/rei/UDC"
        if os.path.exists(udc_bound_path):
            try:
                with open(udc_bound_path, "r") as f:
                    content = f.read().strip()
                    if content:
                        return USBMode.HID_KEYBOARD
            except Exception:
                pass

        # Check if hidg0 device node exists
        if os.path.exists("/dev/hidg0"):
            return USBMode.HID_KEYBOARD

        return USBMode.NORMAL

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

        if target_mode == USBMode.HID_KEYBOARD:
            return self._enable_hid_mode()
        else:
            return self._enable_normal_mode()

    def _enable_hid_mode(self) -> Tuple[bool, str]:
        """Enables USB Composite Gadget (HID + RNDIS)."""
        try:
            # 1. Install gadget script
            with open(self.gadget_script_path, "w") as f:
                f.write(USB_GADGET_SCRIPT_TEMPLATE)
            os.chmod(self.gadget_script_path, 0o755)

            # 2. Install systemd service
            service_path = f"/etc/systemd/system/{self.gadget_service_name}"
            with open(service_path, "w") as f:
                f.write(USB_GADGET_SERVICE_TEMPLATE)
            os.chmod(service_path, 0o644)

            # 3. Enable dwc2 overlay in /boot/config.txt if not present
            boot_cfg = self._find_boot_config()
            if boot_cfg:
                with open(boot_cfg, "r") as f:
                    content = f.read()
                if "dtoverlay=dwc2" not in content:
                    with open(boot_cfg, "a") as f:
                        f.write("\n# REI USB Gadget Mode\ndtoverlay=dwc2\n")

            # 4. Reload and start service
            subprocess.run(["systemctl", "daemon-reload"], check=False)
            subprocess.run(["systemctl", "enable", self.gadget_service_name], check=False)
            res = subprocess.run(["systemctl", "restart", self.gadget_service_name], capture_output=True, text=True)

            if res.returncode == 0:
                return True, "Modo Teclado HID activado con exito."
            else:
                return False, f"Error iniciando gadget: {res.stderr[:40]}"

        except PermissionError:
            return False, "Error: Se requieren permisos root."
        except Exception as ex:
            logger.exception(f"Error configuring HID mode: {ex}")
            return False, f"Fallo: {str(ex)[:35]}"

    def _enable_normal_mode(self) -> Tuple[bool, str]:
        """Disables USB Gadget and restores Standard Host mode."""
        try:
            # 1. Stop and disable gadget service
            subprocess.run(["systemctl", "stop", self.gadget_service_name], check=False)
            subprocess.run(["systemctl", "disable", self.gadget_service_name], check=False)

            # 2. Detach UDC manually if mounted
            udc_path = "/sys/kernel/config/usb_gadget/rei/UDC"
            if os.path.exists(udc_path):
                try:
                    with open(udc_path, "w") as f:
                        f.write("\n")
                except Exception:
                    pass

            return True, "Modo Normal activado con exito."

        except Exception as ex:
            logger.exception(f"Error configuring Normal USB mode: {ex}")
            return False, f"Fallo: {str(ex)[:35]}"
