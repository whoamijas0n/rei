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
    HID_KEYBOARD = "MODO_TECLADO_HID"


class USBModeManager:
    """
    Manages switching and persistence of USB operating modes between
    pure Host mode and USB Gadget (HID Keyboard / libcomposite).
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
        """Determines active USB profile from system state or config.txt."""
        if self.dry_run:
            return self._mock_mode

        # Check config.txt dr_mode
        cfg = self._find_boot_config()
        if os.path.exists(cfg):
            try:
                with open(cfg, "r") as f:
                    content = f.read()
                    if "dr_mode=peripheral" in content:
                        return USBMode.HID_KEYBOARD
                    elif "dr_mode=host" in content:
                        return USBMode.NORMAL
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

                # Unbind ConfigFS gadgets in runtime if active
                subprocess.run(
                    "sudo sh -c 'for d in /sys/kernel/config/usb_gadget/*; do [ -d \"$d\" ] && echo \"\" > \"$d/UDC\" 2>/dev/null || true; done'",
                    shell=True,
                    check=False
                )

                logger.info("USB controller configured as Pure Host.")
                return True, "Modo Host (Normal) configurado con exito."

            else:
                # Configure Peripheral / Gadget mode (Rubber Ducky Keyboard)
                subprocess.run(f"sudo sh -c 'echo \"dtoverlay=dwc2,dr_mode=peripheral\" >> {cfg}'", shell=True, check=False)
                subprocess.run("sudo systemctl enable usb_gadget.service", shell=True, stderr=subprocess.DEVNULL, check=False)

                # Dynamic libcomposite generator script
                sh_script = """#!/bin/bash
modprobe libcomposite
cd /sys/kernel/config/usb_gadget/ 2>/dev/null || exit 0

# Limpieza total de gadgets previos
for dir in /sys/kernel/config/usb_gadget/*; do
    if [ -d "$dir" ]; then
        echo "" > "$dir/UDC" 2>/dev/null
        sleep 0.2
        rm -rf "$dir" 2>/dev/null
    fi
done
if [ -d rei ]; then
    echo "" > rei/UDC 2>/dev/null
    sleep 0.2
    rm -rf rei 2>/dev/null
fi

mkdir -p rei
cd rei

# Descriptores USB
echo 0x1d6b > idVendor
echo 0x0104 > idProduct
echo 0x0100 > bcdDevice
echo 0x0200 > bcdUSB

mkdir -p strings/0x409
echo "fedcba9876543210" > strings/0x409/serialnumber
echo "REI" > strings/0x409/manufacturer
echo "REI HID (Keyboard)" > strings/0x409/product

mkdir -p configs/c.1/strings/0x409
echo "Config 1" > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower

# Funcion HID Teclado
mkdir -p functions/hid.usb0
echo 1 > functions/hid.usb0/protocol
echo 1 > functions/hid.usb0/subclass
echo 8 > functions/hid.usb0/report_length
echo -ne \\x05\\x01\\x09\\x06\\xa1\\x01\\x05\\x07\\x19\\xe0\\x29\\xe7\\x15\\x00\\x25\\x01\\x75\\x01\\x95\\x08\\x81\\x02\\x95\\x01\\x75\\x08\\x81\\x03\\x95\\x05\\x75\\x01\\x05\\x08\\x19\\x01\\x29\\x05\\x91\\x02\\x95\\x01\\x75\\x03\\x91\\x03\\x95\\x06\\x75\\x08\\x15\\x00\\x25\\x65\\x05\\x07\\x19\\x00\\x29\\x65\\x81\\x00\\xc0 > functions/hid.usb0/report_desc
ln -s functions/hid.usb0 configs/c.1/ 2>/dev/null || true

# Enlazar al controlador UDC si esta disponible
UDC_DEV=$(ls /sys/class/udc 2>/dev/null | head -n 1)
if [ -n "$UDC_DEV" ]; then
    echo "$UDC_DEV" > UDC
fi
"""
                # Write script with root privileges
                subprocess.run(
                    f"sudo sh -c 'cat > {gadget_script} << \"EOF\"\n{sh_script}EOF'",
                    shell=True,
                    check=False
                )
                subprocess.run(f"sudo chmod 755 {gadget_script}", shell=True, check=False)

                # Execute gadget script directly in runtime
                res = subprocess.run(f"sudo /bin/bash {gadget_script}", shell=True, capture_output=True, text=True)
                if res.returncode == 0:
                    logger.info("USB HID Keyboard gadget initialized successfully.")
                    return True, "Modo Teclado HID activado con exito."
                else:
                    logger.warning(f"Gadget execution note: {res.stderr[:60]}")
                    return True, "Modo HID configurado (requiere reinicio si dwc2 es nuevo)."

        except Exception as ex:
            logger.exception(f"Error switching USB mode: {ex}")
            return False, f"Fallo: {str(ex)[:35]}"
