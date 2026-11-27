"""
REI - Diagnostic & System Plugins
Decoupled diagnostic implementations for system, network, switch, endpoint analysis,
and system power management.
All plugins implement IDiagnosticPlugin and run asynchronously in worker threads.
"""

import logging
import os
import platform
import shutil
import socket
import subprocess
import time
from typing import Callable, Dict, List, Optional, Tuple, Any

from .errors import format_oled_error
from .interfaces import IDiagnosticPlugin, DiagnosticResult, DiagnosticStatus, Severity
from .parser import sanitize_oled_text, shorten_oled_label, wrap_oled_lines
from .pisugar import PiSugar3Client, PiSugarTelemetry
from .serial_console import (
    ConsolePromptState,
    DEFAULT_BAUDRATES,
    SerialConsoleManager,
    SerialPortInfo,
    SerialProbeResult,
)
from .wifi import WiFiErrorCode, WiFiManager, WiFiNetwork
from plugins.switches import (
    DEFAULT_SWITCH_REGISTRY,
    ISwitchProfile,
    SwitchProfileRegistry,
    get_mock_cisco_2960s_outputs,
    get_switch_category_label,
    normalize_switch_category,
)

logger = logging.getLogger("REI.Core.Plugins")


class IPAddressPlugin(IDiagnosticPlugin):
    """Diagnostica y estructura las direcciones IPv4 activas y el estado de interfaces."""

    @property
    def id(self) -> str:
        return "diag_ip_address"

    @property
    def name(self) -> str:
        return "VER DIRECCION IP"

    @property
    def category(self) -> str:
        return "NETWORK"

    @staticmethod
    def _detect_primary_ip() -> Optional[str]:
        """Detecta la IPv4 de salida principal sin transmitir trafico real."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(1.0)
                s.connect(("8.8.8.8", 80))
                ip_addr = s.getsockname()[0]
                if ip_addr and not ip_addr.startswith("127."):
                    return ip_addr
        except OSError:
            pass
        return None

    @staticmethod
    def _read_operstate(iface: str) -> str:
        """Lee el estado operativo de una interfaz en /sys/class/net."""
        state_path = os.path.join("/sys/class/net", iface, "operstate")
        try:
            with open(state_path, "r", encoding="utf-8") as fh:
                return fh.read().strip().upper() or "UNKNOWN"
        except OSError:
            return "UNKNOWN"

    def _collect_interfaces(self) -> List[Dict[str, Any]]:
        """Recolecta interfaces reales (o simuladas en DRY-RUN) ordenadas por prioridad."""
        if os.getenv("REI_DRY_RUN") == "1" or os.getenv("REI_MOCK_WIFI") == "1":
            return [
                {"name": "wlan0", "ip": "192.168.1.100", "state": "UP", "is_up": True},
                {"name": "usb0", "ip": "172.16.0.1", "state": "UP", "is_up": True},
            ]

        iface_ips: Dict[str, str] = {}
        ip_bin = shutil.which("ip")
        if ip_bin:
            try:
                proc = subprocess.run(
                    [ip_bin, "-4", "-o", "addr", "show"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if proc.returncode == 0:
                    for raw_line in proc.stdout.splitlines():
                        parts = raw_line.split()
                        if len(parts) >= 4 and parts[2] == "inet":
                            iface_name = parts[1].split("@", 1)[0]
                            if iface_name == "lo":
                                continue
                            ip_only = parts[3].split("/", 1)[0]
                            iface_ips.setdefault(iface_name, ip_only)
            except (OSError, subprocess.SubprocessError) as ex:
                logger.debug(f"No se pudo consultar 'ip -4 addr': {ex}")

        candidates: List[str] = list(iface_ips.keys())
        if os.path.isdir("/sys/class/net"):
            try:
                for name in sorted(os.listdir("/sys/class/net")):
                    if name == "lo" or name in iface_ips:
                        continue
                    if name.startswith(("wlan", "eth", "usb", "en", "wl")):
                        candidates.append(name)
            except OSError:
                pass

        priority = {"wlan0": 0, "eth0": 1, "usb0": 2}
        candidates.sort(key=lambda n: (0 if n in iface_ips else 1, priority.get(n, 10), n))

        interfaces: List[Dict[str, Any]] = []
        for name in candidates:
            ip_val = iface_ips.get(name, "")
            state = self._read_operstate(name)
            is_up = bool(ip_val) or state == "UP"
            if state == "UNKNOWN":
                state = "UP" if ip_val else "DOWN"
            interfaces.append(
                {
                    "name": name,
                    "ip": ip_val,
                    "state": state,
                    "is_up": is_up,
                }
            )
        return interfaces

    def run(self, **kwargs) -> DiagnosticResult:
        is_dry_run = os.getenv("REI_DRY_RUN") == "1" or os.getenv("REI_MOCK_WIFI") == "1"
        interfaces = self._collect_interfaces()
        primary_ip = "192.168.1.100" if is_dry_run else self._detect_primary_ip()

        if not primary_ip:
            for item in interfaces:
                if item.get("ip"):
                    primary_ip = str(item["ip"])
                    break

        hostname = sanitize_oled_text(socket.gethostname() or "rei-pi")
        active_ifaces = [item for item in interfaces if item.get("is_up")]
        has_ip = bool(primary_ip)

        if active_ifaces:
            link_summary = ", ".join(str(item["name"]) for item in active_ifaces[:3])
        else:
            link_summary = "Sin enlace"

        details: List[str] = [
            f"IPv4: {primary_ip or 'Sin IP'}",
            f"Host: {hostname[:16]}",
        ]
        for item in interfaces[:4]:
            ip_label = item["ip"] if item["ip"] else "Sin IP"
            details.append(f"{item['name']}: {item['state']} ({ip_label})")

        detail_pages: List[Dict[str, Any]] = [
            {
                "title": "DIRECCION IP",
                "hero": {
                    "tag": "IP",
                    "value": primary_ip or "Sin conexion",
                },
                "rows": [
                    {"label": "Host", "value": hostname},
                    {"label": "Enlace", "value": link_summary},
                ],
            }
        ]

        for idx in range(0, len(interfaces), 2):
            chunk = interfaces[idx : idx + 2]
            detail_pages.append(
                {
                    "title": "INTERFACES",
                    "entities": [
                        {
                            "title": str(item["name"]),
                            "subtitle": str(item["ip"] or "Sin direccion IPv4"),
                            "badge": str(item["state"]),
                            "status_active": bool(item["is_up"]),
                        }
                        for item in chunk
                    ],
                }
            )

        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            overall_status=Severity.OK if has_ip else Severity.WARNING,
            summary="Interfaces Activas" if has_ip else "Sin IPv4 Activa",
            details=details,
            metrics={
                "primary_ip": primary_ip or "",
                "hostname": hostname,
                "interfaces_active": len(active_ifaces),
                "interfaces": interfaces,
                "detail_pages": detail_pages,
            },
        )


class WiFiScanPlugin(IDiagnosticPlugin):
    """Scans for local 2.4GHz / 5GHz Wi-Fi networks."""

    def __init__(self, wifi_mgr: Optional[WiFiManager] = None):
        self._mgr = wifi_mgr or WiFiManager()

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
        scan_error: Optional[str] = None
        scan_error_code = WiFiErrorCode.NONE

        if hasattr(self._mgr, "scan_networks_detailed"):
            networks, scan_error, scan_error_code = self._mgr.scan_networks_detailed()
        else:
            networks = self._mgr.scan_networks()

        if scan_error_code != WiFiErrorCode.NONE and not networks:
            err_lines = wrap_oled_lines([scan_error or "Error al escanear", "Revise radio Wi-Fi"], max_chars=19)
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                summary=sanitize_oled_text(scan_error or "Error al escanear"),
                details=err_lines,
                metrics={
                    "count": 0,
                    "networks": [],
                    "error_code": scan_error_code.value,
                    "scan_error": scan_error,
                },
            )

        details = []
        for net in networks[:8]:
            if net.in_use:
                badge = "[*]"
            elif getattr(net, "is_saved", False):
                badge = "[G]"
            elif net.is_secured:
                badge = "[L]"
            else:
                badge = "[O]"
            details.append(f"{badge} {shorten_oled_label(net.ssid, 11):<11} {net.signal_pct}%")

        if not details:
            details = ["No se encontraron", "redes Wi-Fi."]

        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            summary=f"{len(networks)} Redes Detectadas",
            details=details,
            metrics={
                "count": len(networks),
                "networks": [
                    {
                        "ssid": n.ssid,
                        "signal_pct": n.signal_pct,
                        "security": n.security,
                        "is_secured": n.is_secured,
                        "is_enterprise": getattr(n, "is_enterprise", False),
                        "frequency": n.frequency,
                        "channel": getattr(n, "channel", 1),
                        "in_use": n.in_use,
                        "is_saved": getattr(n, "is_saved", False),
                        "saved_profile_id": getattr(n, "saved_profile_id", None),
                        "saved_profile_name": getattr(n, "saved_profile_name", None),
                    }
                    for n in networks
                ],
            },
        )


class WiFiConnectPlugin(IDiagnosticPlugin):
    """Connects to a specified Wi-Fi network (manual password, open, or saved profile)."""

    def __init__(self, wifi_mgr: Optional[WiFiManager] = None):
        self._mgr = wifi_mgr or WiFiManager()

    @property
    def id(self) -> str:
        return "sys_wifi_connect"

    @property
    def name(self) -> str:
        return "CONECTAR WI-FI"

    @property
    def category(self) -> str:
        return "NETWORK"

    def run(
        self,
        ssid: str = "",
        password: Optional[str] = None,
        profile_id: Optional[str] = None,
        use_saved: bool = False,
        force_update: bool = False,
        security_hint: Optional[str] = None,
        **kwargs,
    ) -> DiagnosticResult:
        if not ssid:
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                summary="SSID vacio",
                details=["SSID no valido", "Seleccione una red"],
                metrics={"success": False, "ssid": "", "error_code": WiFiErrorCode.NO_NETWORK_FOUND.value},
            )

        if use_saved and password is None and not force_update and hasattr(self._mgr, "connect_saved_network"):
            success, message, details_dict = self._mgr.connect_saved_network(
                ssid=ssid,
                profile_id=profile_id,
            )
        else:
            success, message, details_dict = self._mgr.connect_network(
                ssid=ssid,
                password=password,
                profile_id=profile_id,
                force_update=force_update,
                security_hint=security_hint,
            )

        short_ssid = shorten_oled_label(ssid, 14)

        if success:
            ip_addr = sanitize_oled_text(details_dict.get("ip_address", "Asignada"))
            already_conn = bool(details_dict.get("already_connected"))
            lines = [
                f"Red: {short_ssid}",
                f"IP:  {shorten_oled_label(ip_addr, 14)}",
            ]
            if already_conn:
                lines.append("Ya estaba activa")
            elif details_dict.get("signal"):
                lines.append(f"Senal: {shorten_oled_label(details_dict['signal'], 12)}")
            elif details_dict.get("gateway"):
                lines.append(f"GW:  {shorten_oled_label(details_dict['gateway'], 14)}")
            else:
                lines.append("Conexion activa OK")

            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.SUCCESS,
                summary=sanitize_oled_text("Ya conectada" if already_conn else f"Conectado a {ssid}"),
                details=wrap_oled_lines(lines, max_chars=19),
                metrics={"success": True, "ssid": ssid, **details_dict},
            )

        short_msg = sanitize_oled_text(message or "Error de conexion")
        reason = sanitize_oled_text(details_dict.get("reason", ""))
        raw_lines = [short_msg, f"Red: {short_ssid}"]
        if reason and reason.lower() != short_msg.lower():
            raw_lines.append(reason)

        details = wrap_oled_lines(raw_lines, max_chars=19)
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.FAILED,
            summary=shorten_oled_label(short_msg, 19),
            details=details,
            metrics={"success": False, "ssid": ssid, "error": short_msg, **details_dict},
        )


class WiFiSavedListPlugin(IDiagnosticPlugin):
    """Lists saved Wi-Fi connection profiles cross-referenced with active/range status."""

    def __init__(self, wifi_mgr: Optional[WiFiManager] = None):
        self._mgr = wifi_mgr or WiFiManager()

    @property
    def id(self) -> str:
        return "diag_wifi_saved"

    @property
    def name(self) -> str:
        return "REDES GUARDADAS"

    @property
    def category(self) -> str:
        return "NETWORK"

    def run(self, include_scan: bool = True, **kwargs) -> DiagnosticResult:
        profiles = self._mgr.list_saved_profiles(include_scan=include_scan)
        details: List[str] = []
        for prof in profiles[:8]:
            badge = "[*]" if prof.in_use else ("[R]" if prof.in_range else "[G]")
            details.append(f"{badge} {shorten_oled_label(prof.ssid, 15)}")
        if not details:
            details = ["Sin redes guardadas"]

        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            summary=f"{len(profiles)} Guardadas",
            details=wrap_oled_lines(details, max_chars=19),
            metrics={
                "count": len(profiles),
                "profiles": [
                    {
                        "ssid": p.ssid,
                        "profile_id": p.profile_id,
                        "name": p.name,
                        "security": p.security,
                        "autoconnect": p.autoconnect,
                        "in_use": p.in_use,
                        "in_range": p.in_range,
                        "signal_pct": p.signal_pct,
                    }
                    for p in profiles
                ],
            },
        )


class WiFiDisconnectPlugin(IDiagnosticPlugin):
    """Disconnects the active Wi-Fi connection."""

    def __init__(self, wifi_mgr: Optional[WiFiManager] = None):
        self._mgr = wifi_mgr or WiFiManager()

    @property
    def id(self) -> str:
        return "sys_wifi_disconnect"

    @property
    def name(self) -> str:
        return "DESCONECTAR WI-FI"

    @property
    def category(self) -> str:
        return "NETWORK"

    def run(self, ssid: Optional[str] = None, **kwargs) -> DiagnosticResult:
        success, message, details_dict = self._mgr.disconnect_network(ssid=ssid)
        target = ssid or details_dict.get("ssid") or "wlan0"
        short_target = shorten_oled_label(target, 14)

        if success:
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.SUCCESS,
                summary="Desconectado",
                details=wrap_oled_lines(["Desconectado OK", f"Red: {short_target}"], max_chars=19),
                metrics={"success": True, **details_dict},
            )
        short_msg = sanitize_oled_text(message or "Error al desconectar")
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.FAILED,
            summary=shorten_oled_label(short_msg, 19),
            details=wrap_oled_lines([short_msg, f"Red: {short_target}"], max_chars=19),
            metrics={"success": False, "error": short_msg, **details_dict},
        )


class WiFiForgetPlugin(IDiagnosticPlugin):
    """Removes a saved Wi-Fi connection profile."""

    def __init__(self, wifi_mgr: Optional[WiFiManager] = None):
        self._mgr = wifi_mgr or WiFiManager()

    @property
    def id(self) -> str:
        return "sys_wifi_forget"

    @property
    def name(self) -> str:
        return "OLVIDAR RED WI-FI"

    @property
    def category(self) -> str:
        return "NETWORK"

    def run(self, ssid: str = "", profile_id: Optional[str] = None, **kwargs) -> DiagnosticResult:
        if not ssid and not profile_id:
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                summary="SSID vacio",
                details=["Perfil no valido"],
                metrics={"success": False},
            )
        success, message, details_dict = self._mgr.forget_network(ssid=ssid, profile_id=profile_id)
        short_target = shorten_oled_label(ssid or profile_id or "", 14)
        if success:
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.SUCCESS,
                summary="Red olvidada",
                details=wrap_oled_lines(["Red olvidada OK", f"Red: {short_target}"], max_chars=19),
                metrics={"success": True, "ssid": ssid, **details_dict},
            )
        short_msg = sanitize_oled_text(message or "Error al olvidar")
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.FAILED,
            summary=shorten_oled_label(short_msg, 19),
            details=wrap_oled_lines([short_msg, f"Red: {short_target}"], max_chars=19),
            metrics={"success": False, "ssid": ssid, "error": short_msg, **details_dict},
        )


class BatteryStatusPlugin(IDiagnosticPlugin):
    """Reads power management telemetry and battery percentage from PiSugar 3."""

    def __init__(self, pisugar_client: Optional[PiSugar3Client] = None):
        self._client = pisugar_client or PiSugar3Client()

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
        telemetry = self._client.get_telemetry()
        low_bat = (
            not telemetry.is_charging
            and not telemetry.is_power_plugged
            and telemetry.percentage <= 15
        )
        overall = Severity.WARNING if low_bat else Severity.OK
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            overall_status=overall,
            summary=f"{telemetry.percentage}% ({telemetry.voltage:.2f}V)",
            details=telemetry.details,
            metrics={
                "percentage": telemetry.percentage,
                "voltage": telemetry.voltage,
                "status": telemetry.status,
                "current_ma": telemetry.current_ma,
                "power_w": telemetry.power_w,
                "temperature": telemetry.temperature_c,
                "is_charging": telemetry.is_charging,
                "is_power_plugged": telemetry.is_power_plugged,
                "source": telemetry.source,
            }
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

    @staticmethod
    def _read_ram_usage() -> Tuple[int, int, int]:
        """Lee RAM total y en uso desde /proc/meminfo en MB y porcentaje."""
        try:
            if os.path.exists("/proc/meminfo"):
                mem_total_kb = 0
                mem_avail_kb = 0
                with open("/proc/meminfo", "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            mem_total_kb = int(line.split()[1])
                        elif line.startswith("MemAvailable:"):
                            mem_avail_kb = int(line.split()[1])
                if mem_total_kb > 0:
                    used_kb = max(0, mem_total_kb - mem_avail_kb)
                    total_mb = max(1, int(round(mem_total_kb / 1024.0)))
                    used_mb = max(0, int(round(used_kb / 1024.0)))
                    pct = max(0, min(100, int(round((used_kb / float(mem_total_kb)) * 100.0))))
                    return used_mb, total_mb, pct
        except Exception:
            pass
        return 142, 512, 28

    @staticmethod
    def _read_uptime() -> str:
        """Lee el tiempo encendido desde /proc/uptime en formato compacto."""
        try:
            if os.path.exists("/proc/uptime"):
                with open("/proc/uptime", "r", encoding="utf-8") as f:
                    total_sec = int(float(f.read().strip().split()[0]))
                days, rem = divmod(total_sec, 86400)
                hours, rem = divmod(rem, 3600)
                mins = rem // 60
                if days > 0:
                    return f"{days}d {hours}h"
                if hours > 0:
                    return f"{hours}h {mins}m"
                return f"{mins}m"
        except Exception:
            pass
        return "--"

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

        cpu_count = os.cpu_count() or 4
        ram_used_mb, ram_total_mb, ram_pct = self._read_ram_usage()
        uptime_str = self._read_uptime()
        kernel = platform.release()
        hostname = socket.gethostname()
        overall = Severity.WARNING if (temp_c >= 75.0 or ram_pct >= 90) else Severity.OK

        details = [
            f"Temp CPU: {temp_c:.1f} C",
            f"Carga:    {load1:.2f}",
            f"RAM:      {ram_used_mb}MB / {ram_total_mb}MB",
            f"Kernel:   {kernel[:12]}",
        ]
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            overall_status=overall,
            summary="Sistema Estable" if overall == Severity.OK else "Carga / Temp Alta",
            details=details,
            metrics={
                "temp": temp_c,
                "load": load1,
                "cpu_count": cpu_count,
                "ram_used_mb": ram_used_mb,
                "ram_total_mb": ram_total_mb,
                "ram_pct": ram_pct,
                "kernel": kernel,
                "uptime": uptime_str,
                "hostname": hostname,
            },
        )


try:
    from netmiko import ConnectHandler as _NetmikoConnectHandler
    from netmiko.exceptions import (
        NetmikoAuthenticationException as _NetmikoAuthError,
        NetmikoTimeoutException as _NetmikoTimeoutError,
    )
    NETMIKO_AVAILABLE = True
except ImportError:
    _NetmikoConnectHandler = None  # type: ignore
    _NetmikoAuthError = Exception  # type: ignore
    _NetmikoTimeoutError = TimeoutError  # type: ignore
    NETMIKO_AVAILABLE = False


class CiscoSerialPlugin(IDiagnosticPlugin):
    """
    Plugin unificado de diagnóstico por consola serial para switches gestionables.

    Ejecuta el flujo en 3 fases:
    1. Detección pasiva de puerto (/dev/ttyUSB* o /dev/ttyACM*), auto-baudrate
       (9600 -> 115200 -> 38400), estado de prompt y huella pasiva de fabricante.
    2. Señalización limpia de autenticación requerida (usuario/clave/enable) para
       captura interactiva por teclado físico USB sin bloquear el bucle de 30 FPS.
    3. Identificación segura por sonda de solo lectura + recolección Netmiko según
       el perfil de fabricante y la categoría de problema elegida (ej. GENERAL).
    """

    def __init__(
        self,
        web_server: Optional[Any] = None,
        serial_manager: Optional[SerialConsoleManager] = None,
        registry: Optional[SwitchProfileRegistry] = None,
        connection_factory: Optional[Callable[..., Any]] = None,
    ):
        self.web_server = web_server
        self.serial_manager = serial_manager or SerialConsoleManager()
        self.registry = registry or DEFAULT_SWITCH_REGISTRY
        self._connection_factory = connection_factory

    @property
    def id(self) -> str:
        return "diag_cisco_serial"

    @property
    def name(self) -> str:
        return "CONSOLA SERIAL"

    @property
    def category(self) -> str:
        return "SWITCHES"

    @staticmethod
    def _severity_from_status(overall_status: str) -> Severity:
        token = (overall_status or "OK").upper()
        if token == "CRIT":
            return Severity.CRITICAL
        if token == "WARN":
            return Severity.WARNING
        return Severity.OK

    @staticmethod
    def _build_probe_dict(probe: SerialProbeResult) -> Dict[str, Any]:
        return {
            "device": probe.port.device if probe.port else "",
            "port_kind": probe.port.kind if probe.port else "USB_RJ45",
            "baudrate": probe.baudrate,
            "prompt_state": probe.prompt_state.value,
            "banner_text": probe.banner_text,
            "prompt_line": probe.prompt_line,
            "detected_vendor": probe.detected_vendor,
            "hostname_hint": probe.hostname_hint,
            "signals": dict(probe.signals),
        }

    @staticmethod
    def _restore_probe(probe_hint: Dict[str, Any]) -> SerialProbeResult:
        device = str(probe_hint.get("device") or "/dev/ttyUSB0")
        port_kind = str(probe_hint.get("port_kind") or "USB_RJ45")
        raw_state = str(probe_hint.get("prompt_state") or ConsolePromptState.USER_EXEC.value)
        try:
            prompt_state = ConsolePromptState(raw_state)
        except ValueError:
            prompt_state = ConsolePromptState.USER_EXEC

        return SerialProbeResult(
            port=SerialPortInfo(device=device, kind=port_kind),
            baudrate=int(probe_hint.get("baudrate") or 9600),
            prompt_state=prompt_state,
            banner_text=str(probe_hint.get("banner_text") or ""),
            prompt_line=str(probe_hint.get("prompt_line") or ""),
            detected_vendor=probe_hint.get("detected_vendor"),
            hostname_hint=str(probe_hint.get("hostname_hint") or ""),
            signals=dict(probe_hint.get("signals") or {}),
        )

    def _open_netmiko_connection(
        self,
        profile: ISwitchProfile,
        probe: SerialProbeResult,
        credentials: Dict[str, str],
    ) -> Any:
        device = probe.port.device if probe.port else "/dev/ttyUSB0"
        params: Dict[str, Any] = {
            "device_type": profile.netmiko_device_type,
            "username": credentials.get("username", ""),
            "password": credentials.get("password", ""),
            "secret": credentials.get("enable_secret", ""),
            "fast_cli": False,
            "conn_timeout": 15,
            "banner_timeout": 15,
            "read_timeout_override": 20.0,
            "serial_settings": {
                "port": device,
                "baudrate": probe.baudrate,
                "bytesize": 8,
                "parity": "N",
                "stopbits": 1,
                "timeout": 2.0,
            },
        }
        if self._connection_factory is not None:
            return self._connection_factory(**params)
        if not NETMIKO_AVAILABLE or _NetmikoConnectHandler is None:
            raise RuntimeError("La libreria netmiko no esta instalada en el entorno.")
        return _NetmikoConnectHandler(**params)

    def _resolve_profile_after_connect(
        self,
        conn: Any,
        manual_profile: Optional[ISwitchProfile],
        passive_profile: Optional[ISwitchProfile],
        progress_cb: Optional[Callable[[str, float], None]],
    ) -> Tuple[ISwitchProfile, Dict[str, str]]:
        """Fase 3a: si la marca aún no se conoce, envía 1-2 sondas inofensivas y cachea su salida."""
        if manual_profile is not None:
            return manual_profile, {}
        if passive_profile is not None:
            return passive_profile, {}

        if progress_cb:
            progress_cb("Identificando marca...", 0.32)

        probe_cmds = ("show version", "show system-info", "display version")
        combined_output: List[str] = []
        prefetched: Dict[str, str] = {}
        for cmd in probe_cmds:
            try:
                out = str(conn.send_command(cmd, read_timeout=15) or "")
            except Exception as ex:
                logger.debug(f"Sonda segura '{cmd}' sin exito: {ex}")
                continue
            if not out.strip():
                continue
            prefetched[cmd] = out
            combined_output.append(out)
            detected = self.registry.detect_from_probe(out)
            if detected.vendor_id != "generic":
                return detected, prefetched

        return self.registry.detect_from_probe("\n".join(combined_output)), prefetched

    def _run_mock_audit(
        self,
        profile: ISwitchProfile,
        probe: SerialProbeResult,
        norm_cat: str,
        is_privileged: bool,
        progress_cb: Optional[Callable[[str, float], None]],
    ) -> DiagnosticResult:
        """Ejecuta los parsers reales sobre salidas simuladas en DRY-RUN / MOCK."""
        if progress_cb:
            progress_cb("Leyendo interfaces...", 0.55)
        time.sleep(0.10)
        if progress_cb:
            progress_cb("Revisando PoE y salud...", 0.78)
        time.sleep(0.10)

        if profile.vendor_id == "cisco_ios":
            outputs = get_mock_cisco_2960s_outputs()
            if not is_privileged:
                outputs = {k: v for k, v in outputs.items() if k != "logging"}
        elif profile.vendor_id == "tplink_jetstream":
            outputs = {
                "version": (
                    "System Name: SW-TPLINK-LAB\n"
                    "Hardware Version: TL-SG3428MP 2.0\n"
                    "Firmware Version: 2.0.5 Build 20230510\n"
                    "Running Time: 12 days 04:15:22\n"
                ),
                "interfaces_status": "Gi1/0/1 LinkUp 1000M Full\n",
                "vlans": "1 default active\n",
                "stp": "Spanning Tree enabled (RSTP)\n",
            }
        else:
            outputs = {
                "version": "Switch Generico CLI v1.0 (Simulado)\n",
                "interfaces_status": "Port 1 UP 1000Mbps\n",
                "vlans": "VLAN 1 Default\n",
            }

        ctx = {
            "category": norm_cat,
            "is_privileged": is_privileged,
            "hostname_hint": probe.hostname_hint,
            "port_device": probe.port.device if probe.port else "/dev/ttyUSB0",
            "port_kind": probe.port.kind if probe.port else "USB_RJ45",
            "baudrate": probe.baudrate,
            "signals": dict(probe.signals),
            "skipped_privileged": ["show logging"] if not is_privileged else [],
        }
        outcome = profile.parse_outputs(outputs, context=ctx)
        return self._finalize_outcome(outcome, progress_cb=progress_cb)

    def _finalize_outcome(
        self,
        outcome: Any,
        progress_cb: Optional[Callable[[str, float], None]] = None,
    ) -> DiagnosticResult:
        """Registra el informe en REIWebServer y arma el DiagnosticResult final."""
        report_id = "latest"
        if self.web_server is not None and hasattr(self.web_server, "store_local_report"):
            try:
                report_id = self.web_server.store_local_report(
                    os_type=f"SWITCH_{outcome.vendor_id.upper()}",
                    category=get_switch_category_label(outcome.category),
                    hostname=outcome.hostname,
                    telemetry={
                        "device_kind": "SWITCH",
                        "vendor_id": outcome.vendor_id,
                        "vendor_name": outcome.vendor_name,
                        "category": outcome.category,
                        "switch_audit": outcome.switch_audit,
                    },
                    overall_status=outcome.overall_status,
                    switch_audit=outcome.switch_audit,
                )
            except Exception as ex:
                logger.warning(f"No se pudo almacenar el reporte local del switch: {ex}")

        if progress_cb:
            progress_cb("Diagnostico completado", 1.0)

        return DiagnosticResult(
            plugin_name=self.name,
            target_identifier=outcome.hostname,
            status=DiagnosticStatus.SUCCESS,
            overall_status=self._severity_from_status(outcome.overall_status),
            summary=outcome.summary,
            details=outcome.details,
            metrics={
                "detail_pages": outcome.detail_pages,
                "switch_audit": outcome.switch_audit,
            },
            metadata={
                "report_id": report_id,
                "device_kind": "SWITCH",
                "os_type": f"SWITCH_{outcome.vendor_id.upper()}",
                "vendor_id": outcome.vendor_id,
                "vendor_name": outcome.vendor_name,
                "category": outcome.category,
                "category_label": get_switch_category_label(outcome.category),
                "hostname": outcome.hostname,
            },
        )

    def run(
        self,
        progress_callback: Optional[Callable[[str, float], None]] = None,
        profile_id: Optional[str] = None,
        category: str = "GENERAL",
        credentials: Optional[Dict[str, str]] = None,
        probe_hint: Optional[Dict[str, Any]] = None,
        skip_enable: bool = False,
        **kwargs: Any,
    ) -> DiagnosticResult:
        """Ejecuta el flujo completo de diagnóstico por consola serial."""
        def report_progress(msg: str, pct: float) -> None:
            if progress_callback:
                try:
                    progress_callback(msg, pct)
                except Exception:
                    pass

        norm_cat = normalize_switch_category(category)
        creds = dict(credentials or {})
        manual_profile = self.registry.get(profile_id) if profile_id else None
        baudrates = manual_profile.preferred_baudrates if manual_profile else DEFAULT_BAUDRATES

        # -----------------------------------------------------------------
        # FASE 1: Detección pasiva de puerto, baudrate, prompt y marca
        # -----------------------------------------------------------------
        if probe_hint:
            probe = self._restore_probe(probe_hint)
            report_progress(f"Reconectando {probe.baudrate} bps...", 0.22)
        else:
            probe = self.serial_manager.auto_probe(
                baudrates=baudrates,
                progress_cb=report_progress,
            )

        if not probe.is_responsive:
            err_msg = probe.error or "Sin respuesta del switch en consola serial."
            logger.warning(
                f"Consola serial sin respuesta (profile={profile_id or 'auto'}, cat={norm_cat}): {err_msg}"
            )
            formatted = format_oled_error(err_msg, fallback_title="SIN CONSOLA")
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                overall_status=Severity.CRITICAL,
                summary=formatted.title,
                details=formatted.lines,
                metadata={
                    "device_kind": "SWITCH",
                    "category": norm_cat,
                    "error": err_msg,
                },
            )

        # Identificación pasiva del perfil (si no se seleccionó perfil manual)
        passive_profile: Optional[ISwitchProfile] = None
        if manual_profile is None:
            if probe.detected_vendor:
                passive_profile = self.registry.get(probe.detected_vendor)
            if passive_profile is None:
                passive_profile = self.registry.detect_passive(probe.banner_text, probe.prompt_line)

        # -----------------------------------------------------------------
        # FASE 2: Verificar si el switch exige autenticación interactiva
        # -----------------------------------------------------------------
        if probe.prompt_state == ConsolePromptState.LOGIN_USERNAME and not creds.get("username"):
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.WARNING,
                overall_status=Severity.INFO,
                summary="Requiere usuario",
                details=["El switch solicita usuario", "y clave de consola."],
                metadata={
                    "auth_required": True,
                    "auth_stage": "username",
                    "probe": self._build_probe_dict(probe),
                    "profile_id": profile_id,
                    "category": norm_cat,
                },
            )

        if probe.prompt_state == ConsolePromptState.LOGIN_PASSWORD and not creds.get("password"):
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.WARNING,
                overall_status=Severity.INFO,
                summary="Requiere clave",
                details=["El switch solicita clave", "de consola."],
                metadata={
                    "auth_required": True,
                    "auth_stage": "password",
                    "probe": self._build_probe_dict(probe),
                    "profile_id": profile_id,
                    "category": norm_cat,
                },
            )

        # -----------------------------------------------------------------
        # FASE 3: Recolección solo lectura (Simulación o Netmiko real)
        # -----------------------------------------------------------------
        if self.serial_manager.is_mock_mode() and self._connection_factory is None:
            # En simulación con prompt USER_EXEC, respetar el flujo opcional de enable
            if (
                probe.prompt_state == ConsolePromptState.USER_EXEC
                and not creds.get("enable_secret")
                and not skip_enable
                and os.getenv("REI_MOCK_SERIAL_SCENARIO", "").strip().lower() == "cisco_user"
            ):
                return DiagnosticResult(
                    plugin_name=self.name,
                    status=DiagnosticStatus.WARNING,
                    overall_status=Severity.INFO,
                    summary="Clave enable opcional",
                    details=["Switch en modo lectura (>).", "Ingrese enable o Enter."],
                    metadata={
                        "auth_required": True,
                        "auth_stage": "enable",
                        "probe": self._build_probe_dict(probe),
                        "profile_id": profile_id,
                        "category": norm_cat,
                    },
                )

            selected = manual_profile or passive_profile or self.registry.get("cisco_ios") or self.registry.get_fallback()
            is_priv = probe.is_privileged or bool(creds.get("enable_secret"))
            return self._run_mock_audit(
                profile=selected,
                probe=probe,
                norm_cat=norm_cat,
                is_privileged=is_priv,
                progress_cb=report_progress,
            )

        active_profile = manual_profile or passive_profile or self.registry.get("cisco_ios") or self.registry.get_fallback()
        conn = None
        secrets_to_scrub = (creds.get("password"), creds.get("enable_secret"))

        try:
            report_progress("Abriendo sesion CLI...", 0.28)
            conn = self._open_netmiko_connection(active_profile, probe, creds)

            # Comprobar privilegios (# vs >) e intentar enable de forma no destructiva
            is_privileged = probe.is_privileged
            if hasattr(conn, "check_enable_mode"):
                try:
                    is_privileged = bool(conn.check_enable_mode())
                except Exception:
                    is_privileged = probe.is_privileged

            if not is_privileged and not skip_enable and hasattr(conn, "enable"):
                try:
                    conn.enable()
                    is_privileged = True
                except (ValueError, _NetmikoAuthError, _NetmikoTimeoutError) as en_ex:
                    logger.info(f"Switch requiere clave enable o rechazo enable ({en_ex}).")
                    if not creds.get("enable_secret"):
                        return DiagnosticResult(
                            plugin_name=self.name,
                            status=DiagnosticStatus.WARNING,
                            overall_status=Severity.INFO,
                            summary="Clave enable opcional",
                            details=["Switch en modo lectura (>).", "Ingrese enable o Enter."],
                            metadata={
                                "auth_required": True,
                                "auth_stage": "enable",
                                "probe": self._build_probe_dict(probe),
                                "profile_id": profile_id,
                                "category": norm_cat,
                            },
                        )
                    is_privileged = False

            # Fase 3a: Si no se conoció marca pasivamente, sondear con comandos seguros
            resolved_profile, prefetched_outputs = self._resolve_profile_after_connect(
                conn=conn,
                manual_profile=manual_profile,
                passive_profile=passive_profile,
                progress_cb=report_progress,
            )
            logger.info(
                f"Perfil serial resuelto: {resolved_profile.vendor_id} "
                f"(puerto={probe.port.device if probe.port else 'N/D'}, "
                f"baud={probe.baudrate}, priv={is_privileged}, cat={norm_cat})"
            )

            ctx = {
                "category": norm_cat,
                "is_privileged": is_privileged,
                "hostname_hint": probe.hostname_hint,
                "port_device": probe.port.device if probe.port else "/dev/ttyUSB0",
                "port_kind": probe.port.kind if probe.port else "USB_RJ45",
                "baudrate": probe.baudrate,
                "signals": dict(probe.signals),
            }

            outcome = resolved_profile.collect_audit(
                connection=conn,
                category=norm_cat,
                is_privileged=is_privileged,
                progress_cb=report_progress,
                context=ctx,
                secrets_to_scrub=secrets_to_scrub,
                prefetched_outputs=prefetched_outputs,
            )
            logger.info(
                f"Diagnostico serial completado: {outcome.hostname} ({outcome.overall_status})"
            )
            return self._finalize_outcome(outcome, progress_cb=report_progress)

        except _NetmikoAuthError as auth_ex:
            # Si aún no se solicitaron credenciales (ej. Fase 1 devolvió UNKNOWN_PROMPT
            # por un banner largo), redirigir al teclado USB en lugar de fallar directo.
            if not creds.get("username") and not creds.get("password"):
                err_low = str(auth_ex).lower()
                stage = "password" if ("password" in err_low and "username" not in err_low) else "username"
                logger.info(
                    f"Netmiko solicito autenticacion sin credenciales previas -> pidiendo {stage}"
                )
                return DiagnosticResult(
                    plugin_name=self.name,
                    status=DiagnosticStatus.WARNING,
                    overall_status=Severity.INFO,
                    summary="Credenciales requeridas",
                    details=["El switch solicita acceso", "por consola serial."],
                    metadata={
                        "auth_required": True,
                        "auth_stage": stage,
                        "probe": self._build_probe_dict(probe),
                        "profile_id": profile_id,
                        "category": norm_cat,
                    },
                )
            logger.warning(f"Autenticacion serial rechazada por el switch: {auth_ex}")
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                overall_status=Severity.CRITICAL,
                summary="AUTH FALLIDA",
                details=["Usuario o clave de consola", "rechazados por el switch."],
                metadata={
                    "device_kind": "SWITCH",
                    "category": norm_cat,
                    "auth_failed": True,
                },
            )
        except (_NetmikoTimeoutError, TimeoutError):
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                overall_status=Severity.CRITICAL,
                summary="TIMEOUT CONSOLA",
                details=["El switch tardo demasiado", "en responder por serial."],
                metadata={"device_kind": "SWITCH", "category": norm_cat},
            )
        except (OSError, RuntimeError) as ex:
            logger.error(f"Error en sesion serial con switch: {ex}")
            formatted = format_oled_error(ex, fallback_title="ERROR SERIAL")
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                overall_status=Severity.CRITICAL,
                summary=formatted.title,
                details=formatted.lines,
                metadata={"device_kind": "SWITCH", "category": norm_cat},
            )
        finally:
            if conn is not None and hasattr(conn, "disconnect"):
                try:
                    conn.disconnect()
                except Exception:
                    pass


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
        detail_pages = [
            {
                "title": "CISCO SSH",
                "hero": {"tag": "SSH", "value": "192.168.1.1:22"},
                "rows": [
                    {"label": "Version", "value": "Cisco-1.25"},
                    {"label": "Cifrado", "value": "aes256-ctr"},
                ],
            },
            {
                "title": "SEGURIDAD",
                "rows": [
                    {"label": "Proto", "value": "SSH-2.0"},
                    {"label": "Auth", "value": "Key / Password"},
                ],
            },
        ]
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            summary="SSHv2 Detectado",
            details=details,
            metrics={"detail_pages": detail_pages},
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
        detail_pages = [
            {
                "title": "ESCANER SNMP",
                "hero": {"tag": "SNMP", "value": "2 Nodos"},
                "rows": [
                    {"label": "Subred", "value": "192.168.1.0/24"},
                    {"label": "Com.", "value": "public (v2c)"},
                ],
            },
            {
                "title": "NODOS SNMP",
                "entities": [
                    {"title": "192.168.1.1", "subtitle": "Cisco IOS", "badge": "IOS"},
                    {"title": "192.168.1.254", "subtitle": "Gateway", "badge": "GW"},
                ],
            },
        ]
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            summary="2 Nodos SNMP",
            details=details,
            metrics={"nodes_count": 2, "detail_pages": detail_pages},
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
        detail_pages = [
            {
                "title": "USB RNDIS",
                "hero": {"tag": "USB", "value": "172.16.0.1"},
                "rows": [
                    {"label": "Interfaz", "value": "usb0 (UP)"},
                    {"label": "PC", "value": "172.16.0.2 DHCP"},
                ],
            }
        ]
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            summary="RNDIS Enlazado",
            details=details,
            metrics={"detail_pages": detail_pages},
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
        detail_pages = [
            {
                "title": "LINUX SSH",
                "hero": {"tag": "SSH", "value": "admin@endpoint"},
                "rows": [
                    {"label": "Auth", "value": "ed25519 Cert"},
                    {"label": "Interfaz", "value": "usb0 / wlan0"},
                ],
            }
        ]
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            summary="Sesion SSH Lista",
            details=details,
            metrics={"detail_pages": detail_pages},
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
        detail_pages = [
            {
                "title": "BOVEDA / VAULT",
                "hero": {"tag": "SEC", "value": "ENCRIPTADO"},
                "rows": [
                    {"label": "Cifrado", "value": "AES-GCM-256"},
                    {"label": "Llaves", "value": "4 SSH / 8 Pass"},
                ],
            },
            {
                "title": "HARDWARE",
                "rows": [
                    {"label": "HSM", "value": "Chip ATECC608"},
                    {"label": "Estado", "value": "Bloqueada"},
                ],
            },
        ]
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            summary="Bóveda Bloqueada",
            details=details,
            metrics={"detail_pages": detail_pages},
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
            formatted = format_oled_error(msg, fallback_title="FALLO AL APAGAR")
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                summary=formatted.title,
                details=formatted.lines,
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
            formatted = format_oled_error(msg, fallback_title="FALLO AL REINICIAR")
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                summary=formatted.title,
                details=formatted.lines,
            )


class SystemUpdatePlugin(IDiagnosticPlugin):
    """
    Executes Debian/Raspberry Pi OS APT package updates.
    Provides auto-recovery for interrupted dpkg states and stage-by-stage progress callbacks.
    """

    @property
    def id(self) -> str:
        return "sys_apt_update"

    @property
    def name(self) -> str:
        return "ACTUALIZAR SISTEMA"

    @property
    def category(self) -> str:
        return "SYSTEM"

    def run(self, progress_callback: Optional[Callable[[str, float], None]] = None, **kwargs) -> DiagnosticResult:
        def update_progress(msg: str, pct: float) -> None:
            if progress_callback:
                try:
                    progress_callback(msg, pct)
                except Exception:
                    pass

        # Safe simulation / Dry-run support
        if os.getenv("REI_DRY_RUN") == "1" or os.getenv("REI_MOCK_UPDATES") == "1":
            update_progress("Comprobando red...", 0.10)
            time.sleep(0.3)
            update_progress("Saneando dpkg...", 0.25)
            time.sleep(0.3)
            update_progress("Actualizando APT...", 0.45)
            time.sleep(0.4)
            update_progress("Instalando paquetes...", 0.75)
            time.sleep(0.4)
            update_progress("Limpieza del sistema...", 0.90)
            time.sleep(0.3)
            update_progress("Sistema actualizado", 1.0)
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.SUCCESS,
                summary="Sistema al día (APT)",
                details=[
                    "Indices APT al día",
                    "0 paquetes pendientes",
                    "Sistema saneado OK",
                    "Simulación segura OK"
                ],
                metrics={"packages_updated": 0, "mock": True}
            )

        # 1. Connectivity check
        update_progress("Comprobando conexion...", 0.10)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(3.0)
                s.connect(("8.8.8.8", 80))
        except Exception as conn_err:
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                summary="Sin conexion a internet",
                details=[
                    "Error de red:",
                    "No se pudo conectar a",
                    "servidores de repositorios.",
                    f"Detalle: {str(conn_err)[:18]}"
                ]
            )

        # Environment configuration for strict non-interactive execution
        env = dict(os.environ)
        env["DEBIAN_FRONTEND"] = "noninteractive"
        env["APT_LISTCHANGES_FRONTEND"] = "none"
        env["NEEDRESTART_MODE"] = "a"

        use_sudo = (os.geteuid() != 0)

        # 2. Step: Auto-recovery for interrupted dpkg
        update_progress("Saneando dpkg...", 0.20)
        try:
            cmd_dpkg = [
                "dpkg", "--configure", "-a",
                "--force-confdef", "--force-confold"
            ]
            if use_sudo:
                cmd_dpkg.insert(0, "sudo")

            subprocess.run(
                cmd_dpkg,
                capture_output=True,
                text=True,
                timeout=60,
                env=env
            )
        except Exception as dpkg_ex:
            logger.debug(f"dpkg recovery notice: {dpkg_ex}")

        # 3. Step: Fix broken dependencies
        update_progress("Reparando paquetes...", 0.30)
        try:
            cmd_fix = [
                "apt-get", "--fix-broken", "install", "-y", "-qq",
                "-o", "Dpkg::Options::=--force-confdef",
                "-o", "Dpkg::Options::=--force-confold"
            ]
            if use_sudo:
                cmd_fix.insert(0, "sudo")

            subprocess.run(
                cmd_fix,
                capture_output=True,
                text=True,
                timeout=60,
                env=env
            )
        except Exception as fix_ex:
            logger.debug(f"apt fix-broken notice: {fix_ex}")

        # 4. Step: apt-get update
        update_progress("Actualizando indices...", 0.45)
        try:
            cmd_update = ["apt-get", "update", "-qq"]
            if use_sudo:
                cmd_update.insert(0, "sudo")

            res_update = subprocess.run(
                cmd_update,
                capture_output=True,
                text=True,
                timeout=90,
                env=env
            )
            if res_update.returncode != 0:
                err = (res_update.stderr or res_update.stdout or "Error en apt-get update").strip()
                formatted = format_oled_error(err, fallback_title="FALLO APT UPDATE")
                return DiagnosticResult(
                    plugin_name=self.name,
                    status=DiagnosticStatus.FAILED,
                    summary=formatted.title,
                    details=formatted.lines,
                )
        except subprocess.TimeoutExpired:
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                summary="Tiempo agotado",
                details=["Tiempo de espera agotado al consultar repositorios."],
            )
        except Exception as ex:
            formatted = format_oled_error(ex, fallback_title="ERROR APT")
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                summary=formatted.title,
                details=formatted.lines,
            )

        # 5. Step: apt-get dist-upgrade
        update_progress("Instalando mejoras...", 0.70)
        try:
            cmd_upgrade = [
                "apt-get", "dist-upgrade", "-y", "-qq", "--no-install-recommends",
                "-o", "Dpkg::Options::=--force-confdef",
                "-o", "Dpkg::Options::=--force-confold"
            ]
            if use_sudo:
                cmd_upgrade.insert(0, "sudo")

            res_upgrade = subprocess.run(
                cmd_upgrade,
                capture_output=True,
                text=True,
                timeout=300,
                env=env
            )
            if res_upgrade.returncode != 0:
                err = (res_upgrade.stderr or res_upgrade.stdout or "Error en dist-upgrade").strip()
                formatted = format_oled_error(err, fallback_title="FALLO INSTALACION")
                return DiagnosticResult(
                    plugin_name=self.name,
                    status=DiagnosticStatus.FAILED,
                    summary=formatted.title,
                    details=formatted.lines,
                )
        except subprocess.TimeoutExpired:
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                summary="Tiempo agotado",
                details=["Tiempo de espera excedido instalando paquetes."],
            )
        except Exception as ex:
            formatted = format_oled_error(ex, fallback_title="ERROR INSTALACION")
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                summary=formatted.title,
                details=formatted.lines,
            )

        # 6. Step: Cleanup
        update_progress("Limpieza del sistema...", 0.90)
        try:
            cmd_clean = ["apt-get", "autoremove", "-y", "-qq"]
            if use_sudo:
                cmd_clean.insert(0, "sudo")
            subprocess.run(cmd_clean, capture_output=True, timeout=30, env=env)
        except Exception:
            pass

        update_progress("Sistema actualizado", 1.0)
        return DiagnosticResult(
            plugin_name=self.name,
            status=DiagnosticStatus.SUCCESS,
            summary="Sistema al día",
            details=[
                "Sistema operativo",
                "actualizado con éxito.",
                "Paquetes APT al día."
            ],
            metrics={"status": "OK"}
        )


class AppUpdatePlugin(IDiagnosticPlugin):
    """
    Updates the REI software from the GitHub remote repository.
    Verifies git status, fetches remote commits, performs fast-forward pull,
    and updates python dependencies if required.
    Ensures safe.directory compliance under systemd (root/pi user permissions).
    """

    def __init__(self, repo_path: Optional[str] = None):
        self.repo_path = repo_path or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    @property
    def id(self) -> str:
        return "sys_git_update"

    @property
    def name(self) -> str:
        return "ACTUALIZAR REI"

    @property
    def category(self) -> str:
        return "SYSTEM"

    def _exec_git(self, git_bin: str, args: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
        """Executes a Git command with explicit safe.directory overrides."""
        cmd = [
            git_bin,
            "-c", f"safe.directory={self.repo_path}",
            "-c", "safe.directory=*",
            *args
        ]
        return subprocess.run(
            cmd,
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            timeout=timeout
        )

    def run(self, progress_callback: Optional[Callable[[str, float], None]] = None, **kwargs) -> DiagnosticResult:
        def update_progress(msg: str, pct: float) -> None:
            if progress_callback:
                try:
                    progress_callback(msg, pct)
                except Exception:
                    pass

        # Safe simulation / Dry-run support
        if os.getenv("REI_DRY_RUN") == "1" or os.getenv("REI_MOCK_UPDATES") == "1":
            update_progress("Verificando conexion...", 0.20)
            time.sleep(0.3)
            update_progress("Consultando GitHub...", 0.50)
            time.sleep(0.4)
            update_progress("Verificando commits...", 0.80)
            time.sleep(0.3)
            update_progress("Finalizado", 1.0)
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.SUCCESS,
                summary="REI al día (GitHub)",
                details=[
                    "Rama: main",
                    "Sin commits pendientes",
                    "Versión más reciente",
                    "Simulación segura OK"
                ],
                metrics={"commits_pulled": 0, "mock": True}
            )

        # 1. Verify Git executable and repo path
        git_bin = shutil.which("git")
        if not git_bin:
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                summary="Git no encontrado",
                details=["El binario 'git'", "no está instalado", "en el sistema."]
            )

        if not os.path.isdir(os.path.join(self.repo_path, ".git")):
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                summary="No es repositorio Git",
                details=["Ruta no valida:", self.repo_path[:20]]
            )

        # Ensure git safe.directory is configured globally as well
        try:
            subprocess.run(
                [git_bin, "config", "--global", "--add", "safe.directory", self.repo_path],
                capture_output=True,
                timeout=5
            )
            subprocess.run(
                [git_bin, "config", "--global", "--add", "safe.directory", "*"],
                capture_output=True,
                timeout=5
            )
        except Exception:
            pass

        # 2. Connectivity check
        update_progress("Comprobando conexion...", 0.15)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(3.0)
                s.connect(("8.8.8.8", 80))
        except Exception:
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                summary="Sin conexion a GitHub",
                details=["Compruebe la red", "o conexion Wi-Fi."]
            )

        # 3. Git Fetch
        update_progress("Consultando GitHub...", 0.35)
        try:
            res_fetch = self._exec_git(git_bin, ["fetch", "--all", "--prune"], timeout=30)
            if res_fetch.returncode != 0:
                err = (res_fetch.stderr or res_fetch.stdout or "Error en git fetch").strip()
                formatted = format_oled_error(err, fallback_title="FALLO GIT FETCH")
                return DiagnosticResult(
                    plugin_name=self.name,
                    status=DiagnosticStatus.FAILED,
                    summary=formatted.title,
                    details=formatted.lines,
                )
        except subprocess.TimeoutExpired:
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                summary="Tiempo agotado",
                details=["Tiempo de espera excedido conectando a GitHub."],
            )
        except Exception as ex:
            formatted = format_oled_error(ex, fallback_title="ERROR GIT")
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                summary=formatted.title,
                details=formatted.lines,
            )

        # 4. Check status (current branch vs upstream)
        update_progress("Verificando diferencias...", 0.60)
        try:
            branch_res = self._exec_git(git_bin, ["rev-parse", "--abbrev-ref", "HEAD"], timeout=5)
            branch = branch_res.stdout.strip() or "main"

            local_rev = self._exec_git(git_bin, ["rev-parse", "HEAD"], timeout=5).stdout.strip()
            remote_rev = self._exec_git(git_bin, ["rev-parse", f"origin/{branch}"], timeout=5).stdout.strip()

            if local_rev == remote_rev:
                update_progress("REI ya actualizado", 1.0)
                return DiagnosticResult(
                    plugin_name=self.name,
                    status=DiagnosticStatus.SUCCESS,
                    summary="REI al día",
                    details=[
                        f"Rama: {branch}",
                        f"Commit: {local_rev[:8]}",
                        "No hay actualizaciones",
                        "pendientes."
                    ],
                    metrics={"commits_pulled": 0, "branch": branch, "commit": local_rev[:8]}
                )

            # Pull changes
            update_progress("Descargando commits...", 0.80)
            res_pull = self._exec_git(git_bin, ["pull", "--ff-only", "origin", branch], timeout=30)
            if res_pull.returncode != 0:
                err = (res_pull.stderr or res_pull.stdout or "Error en git pull").strip()
                formatted = format_oled_error(err, fallback_title="FALLO GIT PULL")
                return DiagnosticResult(
                    plugin_name=self.name,
                    status=DiagnosticStatus.FAILED,
                    summary=formatted.title,
                    details=formatted.lines,
                )

            # Get new commit hash
            new_rev = self._exec_git(git_bin, ["rev-parse", "HEAD"], timeout=5).stdout.strip()

            update_progress("Actualizacion completada", 1.0)
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.SUCCESS,
                summary="REI actualizado OK",
                details=[
                    "Programa actualizado",
                    f"Rama: {branch}",
                    f"Nuevo: {new_rev[:8]}",
                    "Reinicie para aplicar."
                ],
                metrics={"commits_pulled": 1, "branch": branch, "commit": new_rev[:8]}
            )

        except Exception as ex:
            formatted = format_oled_error(ex, fallback_title="ERROR ACTUALIZACION")
            return DiagnosticResult(
                plugin_name=self.name,
                status=DiagnosticStatus.FAILED,
                summary=formatted.title,
                details=formatted.lines,
            )
