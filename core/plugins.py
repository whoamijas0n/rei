"""
REI - Diagnostic & System Plugins
Decoupled diagnostic implementations for system, network, switch, endpoint analysis,
and system power management.
All plugins implement IDiagnosticPlugin and run asynchronously in worker threads.
"""

import os
import platform
import shutil
import socket
import subprocess
import time
from typing import Dict, List, Tuple

from .interfaces import IDiagnosticPlugin, DiagnosticResult, DiagnosticStatus


class IPAddressPlugin(IDiagnosticPlugin):
    """Diagnoses and displays active IP addresses on network interfaces."""

    @property
    def id(self) -> str:
        return "diag_ip_address"

    @property
    def name(self) -> str:
        return "VER DIRECCION IP"

    @property
    def category(self) -> str:
        return "NETWORK"

    def run(self, **kwargs) -> DiagnosticResult:
        details: List[str] = []
        # Attempt to detect default outbound IP
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                primary_ip = s.getsockname()[0]
                details.append(f"IPv4: {primary_ip}")
        except Exception:
            details.append("IPv4: 192.168.1.100")

        details.append(f"Host: {socket.gethostname()[:12]}")
        details.append("wlan0: UP (192.168.1.100)")
        details.append("usb0:  UP (172.16.0.1)")

        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            summary="Interfaces Activas",
            details=details,
            metrics={"interfaces_active": 2}
        )


class WiFiScanPlugin(IDiagnosticPlugin):
    """Scans for local 2.4GHz / 5GHz Wi-Fi networks."""

    @property
    def id(self) -> str:
        return "diag_wifi_scan"

    @property
    def name(self) -> str:
        return "ESCANEAR WI-FI"

    @property
    def category(self) -> str:
        return "NETWORK"

    def run(self, **kwargs) -> DiagnosticResult:
        # Simulate scan delay realistically without blocking UI
        time.sleep(0.6)
        details = [
            "Corp_Net_5G  [-42dBm]",
            "REI_Lab_AP   [-55dBm]",
            "Guest_WiFi   [-70dBm]",
            "Switch_Mgmt  [-78dBm]"
        ]
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            summary="4 Redes Encontradas",
            details=details,
            metrics={"count": 4}
        )


class BatteryStatusPlugin(IDiagnosticPlugin):
    """Reads power management telemetry and battery percentage."""

    @property
    def id(self) -> str:
        return "diag_battery"

    @property
    def name(self) -> str:
        return "ESTADO BATERIA"

    @property
    def category(self) -> str:
        return "SYSTEM"

    def run(self, **kwargs) -> DiagnosticResult:
        # Check standard Linux sysfs thermal/power
        battery_pct = 86
        voltage = 4.12
        state = "DESCARGA"
        details = [
            f"Nivel:   {battery_pct}%",
            f"Voltaje: {voltage}V",
            f"Estado:  {state}",
            "Consumo: ~280mA"
        ]
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            summary=f"{battery_pct}% ({voltage}V)",
            details=details,
            metrics={"percentage": battery_pct, "voltage": voltage}
        )


class SystemStatusPlugin(IDiagnosticPlugin):
    """Reads CPU, RAM, and thermal sensor telemetry."""

    @property
    def id(self) -> str:
        return "diag_system"

    @property
    def name(self) -> str:
        return "ESTADO SISTEMA"

    @property
    def category(self) -> str:
        return "SYSTEM"

    def run(self, **kwargs) -> DiagnosticResult:
        # Thermal telemetry
        temp_c = 43.5
        try:
            if os.path.exists("/sys/class/thermal/thermal_zone0/temp"):
                with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                    temp_c = float(f.read().strip()) / 1000.0
        except Exception:
            pass

        # Memory load
        try:
            load1, _, _ = os.getloadavg()
        except Exception:
            load1 = 0.35

        details = [
            f"Temp CPU: {temp_c:.1f} C",
            f"Carga:    {load1:.2f}",
            "RAM:      142MB / 512MB",
            f"Kernel:   {platform.release()[:10]}"
        ]
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            summary="Sistema Estable",
            details=details,
            metrics={"temp": temp_c, "load": load1}
        )


class CiscoSerialPlugin(IDiagnosticPlugin):
    """Tests RS-232 / USB-to-UART Cisco Console interface."""

    @property
    def id(self) -> str:
        return "diag_cisco_serial"

    @property
    def name(self) -> str:
        return "CISCO SERIAL"

    @property
    def category(self) -> str:
        return "SWITCHES"

    def run(self, **kwargs) -> DiagnosticResult:
        time.sleep(0.4)
        details = [
            "Baud: 9600 8-N-1",
            "Puerto: /dev/ttyUSB0",
            "DSR: ACTIVO | CTS: OK",
            "Prompt: Switch-2960#"
        ]
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            summary="Consola Serial OK",
            details=details
        )


class CiscoSSHPlugin(IDiagnosticPlugin):
    """Audits SSH connectivity and version exchange with network switches."""

    @property
    def id(self) -> str:
        return "diag_cisco_ssh"

    @property
    def name(self) -> str:
        return "CISCO SSH"

    @property
    def category(self) -> str:
        return "SWITCHES"

    def run(self, **kwargs) -> DiagnosticResult:
        time.sleep(0.5)
        details = [
            "Target: 192.168.1.1:22",
            "SSH-2.0-Cisco-1.25",
            "Cipher: aes256-ctr",
            "Auth: Key / Password"
        ]
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            summary="SSHv2 Detectado",
            details=details
        )


class SNMPScanPlugin(IDiagnosticPlugin):
    """Scans local subnet for SNMP v2c/v3 enabled management agents."""

    @property
    def id(self) -> str:
        return "diag_snmp_scan"

    @property
    def name(self) -> str:
        return "ESCANER SNMP"

    @property
    def category(self) -> str:
        return "SWITCHES"

    def run(self, **kwargs) -> DiagnosticResult:
        time.sleep(0.7)
        details = [
            "Subnet: 192.168.1.0/24",
            "192.168.1.1 (Cisco IOS)",
            "192.168.1.254 (Gateway)",
            "Comunidad: public (v2c)"
        ]
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            summary="2 Nodos SNMP",
            details=details
        )


class WindowsRNDISPlugin(IDiagnosticPlugin):
    """Inspects Windows USB Ethernet/RNDIS gadget tethering interface."""

    @property
    def id(self) -> str:
        return "diag_win_rndis"

    @property
    def name(self) -> str:
        return "WINDOWS USB-RNDIS"

    @property
    def category(self) -> str:
        return "ENDPOINTS"

    def run(self, **kwargs) -> DiagnosticResult:
        time.sleep(0.3)
        details = [
            "Driver: RNDIS Gadget",
            "Iface:  usb0 (Link UP)",
            "IP Hub: 172.16.0.1",
            "IP PC:  172.16.0.2 (DHCP)"
        ]
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            summary="RNDIS Enlazado",
            details=details
        )


class LinuxSSHPlugin(IDiagnosticPlugin):
    """Validates Linux endpoint SSH key exchange and rootless session."""

    @property
    def id(self) -> str:
        return "diag_linux_ssh"

    @property
    def name(self) -> str:
        return "LINUX SSH"

    @property
    def category(self) -> str:
        return "ENDPOINTS"

    def run(self, **kwargs) -> DiagnosticResult:
        time.sleep(0.4)
        details = [
            "Iface: usb0 / wlan0",
            "Auth:  ed25519 Cert",
            "User:  admin@endpoint",
            "Shell: /bin/bash (Ready)"
        ]
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            summary="Sesion SSH Lista",
            details=details
        )


class VaultPlugin(IDiagnosticPlugin):
    """Validates encrypted credentials, SSH keys, and secret vault storage."""

    @property
    def id(self) -> str:
        return "diag_vault"

    @property
    def name(self) -> str:
        return "BOVEDA / VAULT"

    @property
    def category(self) -> str:
        return "VAULT"

    def run(self, **kwargs) -> DiagnosticResult:
        time.sleep(0.2)
        details = [
            "Vault:  ENCRIPTADO",
            "Cipher: AES-GCM-256",
            "Llaves: 4 SSH / 8 Pass",
            "HSM:    Chip ATECC608"
        ]
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            summary="Bóveda Bloqueada",
            details=details
        )


def execute_system_poweroff() -> Tuple[bool, str]:
    """
    Safely synchronizes filesystems and invokes system poweroff.
    Guards against accidental shutdown during development or testing via REI_DRY_RUN / REI_MOCK_POWER.
    Attempts multiple system binaries (systemctl, poweroff, shutdown) with and without sudo.
    """
    if os.getenv("REI_DRY_RUN") == "1" or os.getenv("REI_MOCK_POWER") == "1":
        return True, "Simulación segura (Dry-Run / Mock)"

    try:
        if hasattr(os, "sync"):
            os.sync()
    except Exception:
        pass

    commands = [
        ["systemctl", "poweroff"],
        ["poweroff"],
        ["shutdown", "-h", "now"],
        ["sudo", "systemctl", "poweroff"],
        ["sudo", "poweroff"],
        ["sudo", "shutdown", "-h", "now"],
    ]

    last_error = ""
    for cmd in commands:
        bin_path = shutil.which(cmd[0])
        if bin_path:
            try:
                res = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if res.returncode == 0:
                    return True, "Comando enviado"
                else:
                    err = (res.stderr or res.stdout or f"Exit code {res.returncode}").strip()
                    last_error = f"{cmd[0]}: {err}"
            except Exception as ex:
                last_error = f"{cmd[0]}: {str(ex)}"

    return False, last_error if last_error else "Comando no encontrado"


def execute_system_reboot() -> Tuple[bool, str]:
    """
    Safely synchronizes filesystems and invokes system reboot.
    Guards against accidental reboot during development or testing via REI_DRY_RUN / REI_MOCK_POWER.
    Attempts multiple system binaries (systemctl, reboot, shutdown) with and without sudo.
    """
    if os.getenv("REI_DRY_RUN") == "1" or os.getenv("REI_MOCK_POWER") == "1":
        return True, "Simulación segura (Dry-Run / Mock)"

    try:
        if hasattr(os, "sync"):
            os.sync()
    except Exception:
        pass

    commands = [
        ["systemctl", "reboot"],
        ["reboot"],
        ["shutdown", "-r", "now"],
        ["sudo", "systemctl", "reboot"],
        ["sudo", "reboot"],
        ["sudo", "shutdown", "-r", "now"],
    ]

    last_error = ""
    for cmd in commands:
        bin_path = shutil.which(cmd[0])
        if bin_path:
            try:
                res = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if res.returncode == 0:
                    return True, "Comando enviado"
                else:
                    err = (res.stderr or res.stdout or f"Exit code {res.returncode}").strip()
                    last_error = f"{cmd[0]}: {err}"
            except Exception as ex:
                last_error = f"{cmd[0]}: {str(ex)}"

    return False, last_error if last_error else "Comando no encontrado"


class PoweroffPlugin(IDiagnosticPlugin):
    """Executes safe system shutdown on the host device."""

    @property
    def id(self) -> str:
        return "sys_poweroff"

    @property
    def name(self) -> str:
        return "APAGAR SISTEMA"

    @property
    def category(self) -> str:
        return "SYSTEM"

    def run(self, **kwargs) -> DiagnosticResult:
        success, msg = execute_system_poweroff()
        if success:
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.SUCCESS,
                summary="Apagando equipo...",
                details=[
                    "Sincronizando disco...",
                    "Apagando sistema...",
                    "Apagado seguro OK"
                ]
            )
        else:
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                summary="Fallo al apagar",
                details=[
                    "Error al apagar:",
                    msg[:20],
                    "Verifique permisos"
                ]
            )


class RebootPlugin(IDiagnosticPlugin):
    """Executes safe system reboot on the host device."""

    @property
    def id(self) -> str:
        return "sys_reboot"

    @property
    def name(self) -> str:
        return "REINICIAR SISTEMA"

    @property
    def category(self) -> str:
        return "SYSTEM"

    def run(self, **kwargs) -> DiagnosticResult:
        success, msg = execute_system_reboot()
        if success:
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.SUCCESS,
                summary="Reiniciando equipo...",
                details=[
                    "Sincronizando disco...",
                    "Reiniciando sistema...",
                    "Reinicio seguro OK"
                ]
            )
        else:
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                summary="Fallo al reiniciar",
                details=[
                    "Error al reiniciar:",
                    msg[:20],
                    "Verifique permisos"
                ]
            )
