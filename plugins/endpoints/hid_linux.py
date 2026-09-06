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
    Constructs compact, non-destructive Bash scripts and lightweight micro-stager commands
    for target Linux endpoints.
    """

    @classmethod
    def normalize_category(cls, category: str) -> str:
        """Normalizes diagnostic category names to standard canonical tokens."""
        cat = category.upper().strip()
        if "RED" in cat or "CONEXION" in cat or "NETWORK" in cat:
            return "RED"
        elif "HARDWARE" in cat or "CPU" in cat:
            return "HARDWARE"
        elif "MALWARE" in cat or "VIRUS" in cat or "PROCESOS" in cat:
            return "MALWARE"
        elif "OTROS" in cat or "LOGS" in cat or "REGISTROS" in cat:
            return "LOGS"
        return "COMPLETO"

    @classmethod
    def get_bash_script(cls, category: str, server_url: str = "http://10.0.0.1:8000") -> str:
        """
        Returns the raw Bash script that gathers telemetry and posts JSON via curl or wget.
        Escapes and cleans outputs to ensure 100% valid JSON payload.
        """
        cat = cls.normalize_category(category)
        endpoint_uri = f"{server_url.rstrip('/')}/api/v1/endpoint/report"

        if cat == "RED":
            telemetry_sh = (
                "ip=$(ip -4 addr show up 2>/dev/null | grep -v '127.0.0.1' | grep inet | awk '{print $2}' | head -n 1 | tr -d '\"\\\\\\r\\n');"
                "gw=$(ip route 2>/dev/null | grep default | awk '{print $3}' | head -n 1 | tr -d '\"\\\\\\r\\n');"
                "ping_ok=$(ping -c 1 -W 2 \"$gw\" >/dev/null 2>&1 && echo true || echo false);"
                "t=\"{\\\"ip\\\":\\\"$ip\\\",\\\"gateway\\\":\\\"$gw\\\",\\\"ping_gateway\\\":$ping_ok}\";"
            )
        elif cat == "HARDWARE":
            telemetry_sh = (
                "cpu=$(top -bn1 2>/dev/null | grep 'Cpu(s)' | awk '{print $2 + $4}' | cut -d'.' -f1 | tr -d '\"\\\\\\r\\n');"
                "ram=$(free 2>/dev/null | grep Mem | awk '{printf(\"%.1f\", $3/$2 * 100.0)}' | tr -d '\"\\\\\\r\\n');"
                "temp=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null | awk '{printf(\"%.1f\", $1/1000)}' | tr -d '\"\\\\\\r\\n' || echo '0');"
                "t=\"{\\\"cpu_percent\\\":${cpu:-0},\\\"ram_percent\\\":${ram:-0},\\\"cpu_temp_c\\\":${temp:-0}}\";"
            )
        elif cat == "MALWARE":
            telemetry_sh = (
                "procs=$(ps -eo comm,%cpu --sort=-%cpu 2>/dev/null | head -n 6 | tail -n +2 | tr -d '\"\\\\\\r\\n' | tr '\\n' ',' | sed 's/,$//');"
                "ports=$(ss -tulpn 2>/dev/null | grep LISTEN | awk '{print $5}' | head -n 5 | tr -d '\"\\\\\\r\\n' | tr '\\n' ',' | sed 's/,$//');"
                "t=\"{\\\"top_procs\\\":\\\"$procs\\\",\\\"listening_ports\\\":\\\"$ports\\\"}\";"
            )
        elif cat == "LOGS":
            telemetry_sh = (
                "failed=$(systemctl --failed --no-legend 2>/dev/null | awk '{print $2}' | tr -d '\"\\\\\\r\\n' | tr '\\n' ',' | sed 's/,$//');"
                "disk=$(df -h / 2>/dev/null | awk 'NR==2 {print $5}' | tr -d '\"\\\\\\r\\n');"
                "t=\"{\\\"failed_services\\\":\\\"$failed\\\",\\\"disk_usage\\\":\\\"$disk\\\"}\";"
            )
        else:
            # ANALISIS COMPLETO (Consolidated Full Suite)
            telemetry_sh = (
                "cpu=$(top -bn1 2>/dev/null | grep 'Cpu(s)' | awk '{print $2 + $4}' | cut -d'.' -f1 | tr -d '\"\\\\\\r\\n');"
                "ram=$(free 2>/dev/null | grep Mem | awk '{printf(\"%.1f\", $3/$2 * 100.0)}' | tr -d '\"\\\\\\r\\n');"
                "ip=$(ip -4 addr show up 2>/dev/null | grep -v '127.0.0.1' | grep inet | awk '{print $2}' | head -n 1 | tr -d '\"\\\\\\r\\n');"
                "gw=$(ip route 2>/dev/null | grep default | awk '{print $3}' | head -n 1 | tr -d '\"\\\\\\r\\n');"
                "t=\"{\\\"cpu_percent\\\":${cpu:-0},\\\"ram_percent\\\":${ram:-0},\\\"ip\\\":\\\"$ip\\\",\\\"gateway\\\":\\\"$gw\\\"}\";"
            )

        script = (
            f"hn=$(hostname 2>/dev/null | tr -d '\"\\\\\\r\\n' || echo 'linux-client');"
            f"{telemetry_sh}"
            f"p=\"{{\\\"os_type\\\":\\\"linux\\\",\\\"category\\\":\\\"{cat}\\\",\\\"hostname\\\":\\\"$hn\\\",\\\"telemetry\\\":$t}}\";"
            f"curl -s -m 10 -X POST -H 'Content-Type: application/json' -d \"$p\" {endpoint_uri} >/dev/null 2>&1 || wget -q --timeout=10 --header='Content-Type: application/json' --post-data=\"$p\" -O- {endpoint_uri} >/dev/null 2>&1"
        )
        return script

    @classmethod
    def get_bash_payload(cls, category: str, server_url: str = "http://10.0.0.1:8000") -> str:
        """
        Returns a compact micro-stager (<150 chars) that downloads and executes
        the full Bash telemetry script from REI's local web server in the background.
        """
        cat = cls.normalize_category(category)
        url = f"{server_url.rstrip('/')}/l/{cat}"
        return f"(curl -s -m 5 {url}||wget -qO- {url})|sh >/dev/null 2>&1 & disown"


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
            if hasattr(web_server, "base_url") and web_server.base_url:
                self._server_url = web_server.base_url

    def run(self, **kwargs) -> DiagnosticResult:
        progress_cb: Optional[Callable[[str, float], None]] = kwargs.get("progress_callback")
        start_time = time.monotonic()

        # Step 0: Validate hardware connection before starting
        is_ready, not_ready_details = self._injector.check_ready()
        if not is_ready:
            return DiagnosticResult(
                plugin_name=self.name,
                target_identifier=f"Linux ({self._layout.upper()})",
                status=DiagnosticStatus.FAILED,
                overall_status=Severity.WARNING,
                summary=not_ready_details[0] if not_ready_details else "USB no listo",
                details=not_ready_details,
            )

        # Step 1: Prepare Payload
        if progress_cb:
            progress_cb("Generando payload...", 0.1)

        payload_cmd = LinuxPayloadGenerator.get_bash_payload(
            category=self._category,
            server_url=self._server_url,
        )

        # Capture watermark before HID typing to eliminate race conditions
        watermark = self._web_server.get_report_watermark() if self._web_server else 0

        # Step 2: Inject via USB HID
        if progress_cb:
            progress_cb("Inyectando HID...", 0.3)

        try:
            # Emulate Ctrl + Alt + t to open terminal on Linux Desktop
            self._injector.press_combination("ctrl+alt", "t")
            time.sleep(1.2)

            # Write bash execution string and press ENTER (leading space avoids history)
            self._injector.write_text(f" {payload_cmd}\n", layout=self._layout)
            time.sleep(0.5)
            # Ensure process is disowned and close terminal cleanly
            self._injector.write_text("disown -a 2>/dev/null || true\nexit\n", layout=self._layout)

        except Exception as inj_ex:
            logger.error(f"HID injection failed: {inj_ex}")
            if not self._injector.dry_run:
                err_str = str(inj_ex).lower()
                if "timeout" in err_str or "no responde" in err_str:
                    details = ["Host no acepta HID", "Verifique cable de datos", "Use puerto USB (no PWR)"]
                    summary = "Timeout USB HID"
                elif "desconectado" in err_str:
                    details = ["Cable desconectado", "Conecte puerto USB a PC", "Use cable de datos"]
                    summary = "USB desconectado"
                else:
                    details = [str(inj_ex)[:20], "Verifique conexión USB"]
                    summary = "Fallo de inyección"

                return DiagnosticResult(
                    plugin_name=self.name,
                    target_identifier=f"Linux ({self._layout.upper()})",
                    status=DiagnosticStatus.FAILED,
                    overall_status=Severity.CRITICAL,
                    summary=summary,
                    details=details,
                )

        # Step 3: Await Telemetry over HTTP
        if progress_cb:
            progress_cb("Esperando telemetría...", 0.6)

        report = None
        if self._web_server:
            report = self._web_server.wait_for_report(watermark=watermark, timeout_seconds=self._timeout_seconds)

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
