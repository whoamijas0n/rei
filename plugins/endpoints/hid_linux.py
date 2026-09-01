"""
REI - Linux USB HID Diagnostic Plugin (plugins/endpoints/hid_linux.py)
Generates non-destructive Bash payloads and orchestrates Rubber Ducky injection
for Linux endpoints with automated JSON exfiltration to REI's local FastAPI server.
"""

import base64
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from core.ducky import DuckyInjector
from core.interfaces import (
    DiagnosticMetric,
    DiagnosticResult,
    DiagnosticStatus,
    IDiagnosticPlugin,
    Severity,
)

logger = logging.getLogger("REI.Plugins.Endpoints.Linux")


class LinuxPayloadGenerator:
    """
    Constructs compact, non-destructive Bash scripts and base64-encoded execution commands
    for target Linux endpoints.
    """

    @classmethod
    def get_bash_script(cls, category: str, server_url: str = "http://10.0.0.1:8000") -> str:
        """
        Returns the raw Bash script that gathers telemetry and posts JSON via curl.
        """
        cat = category.upper().strip()
        endpoint_uri = f"{server_url.rstrip('/')}/api/v1/endpoint/report"

        if "RED" in cat or "CONEXION" in cat or "NETWORK" in cat:
            telemetry_sh = (
                "ip=$(ip -4 addr show up | grep -v '127.0.0.1' | grep inet | awk '{print $2}' | head -n 1);"
                "gw=$(ip route | grep default | awk '{print $3}' | head -n 1);"
                "ping_ok=$(ping -c 1 -W 2 \"$gw\" >/dev/null 2>&1 && echo true || echo false);"
                "t=\"{\\\"ip\\\":\\\"$ip\\\",\\\"gateway\\\":\\\"$gw\\\",\\\"ping_gateway\\\":$ping_ok}\";"
            )
        elif "HARDWARE" in cat or "CPU" in cat:
            telemetry_sh = (
                "cpu=$(top -bn1 | grep 'Cpu(s)' | awk '{print $2 + $4}' | cut -d'.' -f1);"
                "ram=$(free | grep Mem | awk '{printf(\"%.1f\", $3/$2 * 100.0)}');"
                "temp=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null | awk '{printf(\"%.1f\", $1/1000)}' || echo '0');"
                "t=\"{\\\"cpu_percent\\\":${cpu:-0},\\\"ram_percent\\\":${ram:-0},\\\"cpu_temp_c\\\":${temp:-0}}\";"
            )
        elif "MALWARE" in cat or "VIRUS" in cat:
            telemetry_sh = (
                "procs=$(ps -eo comm,%cpu --sort=-%cpu 2>/dev/null | head -n 6 | tail -n +2 | tr '\\n' ',' | sed 's/,$//');"
                "ports=$(ss -tulpn 2>/dev/null | grep LISTEN | awk '{print $5}' | head -n 5 | tr '\\n' ',' | sed 's/,$//');"
                "t=\"{\\\"top_procs\\\":\\\"$procs\\\",\\\"listening_ports\\\":\\\"$ports\\\"}\";"
            )
        elif "OTROS" in cat or "LOGS" in cat:
            telemetry_sh = (
                "failed=$(systemctl --failed --no-legend 2>/dev/null | awk '{print $2}' | tr '\\n' ',' | sed 's/,$//');"
                "disk=$(df -h / 2>/dev/null | awk 'NR==2 {print $5}');"
                "t=\"{\\\"failed_services\\\":\\\"$failed\\\",\\\"disk_usage\\\":\\\"$disk\\\"}\";"
            )
        else:
            # ANALISIS COMPLETO (Consolidated Full Suite)
            telemetry_sh = (
                "cpu=$(top -bn1 | grep 'Cpu(s)' | awk '{print $2 + $4}' | cut -d'.' -f1);"
                "ram=$(free | grep Mem | awk '{printf(\"%.1f\", $3/$2 * 100.0)}');"
                "ip=$(ip -4 addr show up | grep -v '127.0.0.1' | grep inet | awk '{print $2}' | head -n 1);"
                "gw=$(ip route | grep default | awk '{print $3}' | head -n 1);"
                "t=\"{\\\"cpu_percent\\\":${cpu:-0},\\\"ram_percent\\\":${ram:-0},\\\"ip\\\":\\\"$ip\\\",\\\"gateway\\\":\\\"$gw\\\"}\";"
            )

        script = (
            f"hn=$(hostname 2>/dev/null || echo 'linux-client');"
            f"{telemetry_sh}"
            f"p=\"{{\\\"os_type\\\":\\\"linux\\\",\\\"category\\\":\\\"{cat}\\\",\\\"hostname\\\":\\\"$hn\\\",\\\"telemetry\\\":$t}}\";"
            f"curl -s -m 10 -X POST -H 'Content-Type: application/json' -d \"$p\" {endpoint_uri} >/dev/null 2>&1"
        )
        return script

    @classmethod
    def get_bash_payload(cls, category: str, server_url: str = "http://10.0.0.1:8000") -> str:
        """
        Returns a single-line Base64-encoded command to execute in background without quoting conflicts.
        """
        script = cls.get_bash_script(category=category, server_url=server_url)
        b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
        return f"echo {b64}|base64 -d|sh >/dev/null 2>&1 &"


class LinuxHIDPlugin(IDiagnosticPlugin):
    """
    Decoupled plugin that injects Linux diagnostic commands via Rubber Ducky
    and waits for telemetry report over HTTP.
    """

    def __init__(
        self,
        category: str = "ANALISIS COMPLETO",
        keyboard_layout: str = "es",
        server_url: str = "http://10.0.0.1:8000",
        timeout_seconds: float = 25.0,
        injector: Optional[DuckyInjector] = None,
        web_server: Optional[Any] = None,
    ):
        self._category = category.upper()
        self._layout = keyboard_layout.lower()
        self._server_url = server_url
        self._timeout_seconds = timeout_seconds
        self._injector = injector or DuckyInjector()
        self._web_server = web_server

    @property
    def id(self) -> str:
        return "diag_linux_hid"

    @property
    def name(self) -> str:
        return f"LINUX {self._category[:12]} ({self._layout.upper()})"

    @property
    def category(self) -> str:
        return "ENDPOINTS"

    def set_context(self, category: str, layout: str, web_server: Optional[Any] = None) -> None:
        """Dynamically updates execution context."""
        self._category = category.upper()
        self._layout = layout.lower()
        if web_server:
            self._web_server = web_server

    def run(self, **kwargs) -> DiagnosticResult:
        progress_cb: Optional[Callable[[str, float], None]] = kwargs.get("progress_callback")
        start_time = time.monotonic()

        # Step 1: Prepare Payload
        if progress_cb:
            progress_cb("Generando payload...", 0.1)

        payload_cmd = LinuxPayloadGenerator.get_bash_payload(
            category=self._category,
            server_url=self._server_url,
        )

        # Step 2: Inject via USB HID
        if progress_cb:
            progress_cb("Inyectando HID...", 0.3)

        try:
            # Emulate Ctrl + Alt + t to open terminal on Linux Desktop
            self._injector.press_combination("ctrl+alt", "t")
            time.sleep(1.0)

            # Write bash execution string and press ENTER (leading space avoids history)
            self._injector.write_text(f" {payload_cmd}\n", layout=self._layout)
            time.sleep(0.3)
            # Close terminal cleanly
            self._injector.write_text("exit\n", layout=self._layout)

        except Exception as inj_ex:
            logger.error(f"HID injection failed: {inj_ex}")
            if not self._injector.dry_run:
                return DiagnosticResult(
                    plugin_name=self.name,
                    target_identifier=f"Linux ({self._layout.upper()})",
                    status=DiagnosticStatus.FAILED,
                    overall_status=Severity.CRITICAL,
                    summary="Fallo de inyección USB HID",
                    details=["USB HID no disponible", "Active Modo HID en Menu"],
                )

        # Step 3: Await Telemetry over HTTP
        if progress_cb:
            progress_cb("Esperando telemetría...", 0.6)

        report = None
        if self._web_server:
            report = self._web_server.wait_for_report(timeout_seconds=self._timeout_seconds)

        # If in dry-run or simulated
        if not report:
            if self._injector.dry_run:
                if progress_cb:
                    progress_cb("Simulando reporte...", 0.85)
                time.sleep(0.5)
                report_data = {
                    "os_type": "LINUX",
                    "category": self._category,
                    "hostname": "linux-client",
                    "telemetry": {
                        "cpu_percent": 12.4,
                        "ram_percent": 38.0,
                        "ip": "10.0.0.3/24",
                        "gateway": "10.0.0.1",
                    },
                }
                rep_id = (
                    self._web_server.store_local_report(
                        os_type="LINUX",
                        category=self._category,
                        hostname="linux-client",
                        telemetry=report_data["telemetry"],
                    )
                    if self._web_server
                    else "mock-linux"
                )
                report = type("StoredReportMock", (), {
                    "report_id": rep_id,
                    "hostname": "linux-client",
                    "os_type": "LINUX",
                    "category": self._category,
                    "telemetry": report_data["telemetry"],
                    "overall_status": "OK",
                    "ai_analysis": None,
                })()

        if not report:
            return DiagnosticResult(
                plugin_name=self.name,
                target_identifier=f"Linux ({self._layout.upper()})",
                status=DiagnosticStatus.FAILED,
                overall_status=Severity.WARNING,
                summary="Timeout esperando reporte",
                details=["No se recibió HTTP POST", "Verifique cable y red RNDIS/ECM"],
            )

        # Step 4: Build Metrics & Result
        if progress_cb:
            progress_cb("Procesando métricas...", 0.95)

        metrics: List[DiagnosticMetric] = []
        details: List[str] = [
            f"Host: {getattr(report, 'hostname', 'Linux')[:14]}",
            f"Cat:  {self._category[:14]}",
        ]

        t_data = getattr(report, "telemetry", {})
        cpu = t_data.get("cpu_percent")
        if cpu is not None:
            c_val = f"{cpu}%"
            c_sev = Severity.CRITICAL if float(cpu) > 90 else (Severity.WARNING if float(cpu) > 75 else Severity.OK)
            metrics.append(DiagnosticMetric(name="Uso CPU", value=c_val, status=c_sev))
            details.append(f"CPU:  {c_val}")

        ram = t_data.get("ram_percent")
        if ram is not None:
            r_val = f"{ram}%"
            r_sev = Severity.CRITICAL if float(ram) > 90 else Severity.OK
            metrics.append(DiagnosticMetric(name="Uso RAM", value=r_val, status=r_sev))
            details.append(f"RAM:  {r_val}")

        ip = t_data.get("ip")
        if ip:
            metrics.append(DiagnosticMetric(name="IP Host", value=str(ip)[:15], status=Severity.INFO))
            details.append(f"IP:   {str(ip)[:14]}")

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        return DiagnosticResult(
            plugin_name=self.name,
            target_identifier=f"Linux ({getattr(report, 'hostname', 'Host')})",
            execution_time_ms=elapsed_ms,
            status=DiagnosticStatus.SUCCESS,
            overall_status=Severity.OK,
            summary=f"Diagnóstico {self._category} OK",
            details=details[:4],
            metrics=metrics,
            raw_output=json.dumps(t_data),
            metadata={"report_id": getattr(report, "report_id", "latest"), "os_type": "LINUX"},
        )
