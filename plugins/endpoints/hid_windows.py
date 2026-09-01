"""
REI - Windows USB HID Diagnostic Plugin (plugins/endpoints/hid_windows.py)
Generates non-destructive PowerShell payloads and orchestrates Rubber Ducky injection
for Windows endpoints with automated JSON exfiltration to REI's local FastAPI server.
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

logger = logging.getLogger("REI.Plugins.Endpoints.Windows")


class WindowsPayloadGenerator:
    """
    Constructs compact, non-destructive PowerShell scripts and base64-encoded execution commands
    for target Windows endpoints.
    """

    @classmethod
    def get_powershell_script(cls, category: str, server_url: str = "http://10.0.0.1:8000") -> str:
        """
        Returns the raw PowerShell script that gathers telemetry and posts JSON to server_url.
        """
        cat = category.upper().strip()
        endpoint_uri = f"{server_url.rstrip('/')}/api/v1/endpoint/report"

        if "RED" in cat or "CONEXION" in cat or "NETWORK" in cat:
            telemetry_ps = """
            $gw=(Get-CimInstance Win32_NetworkAdapterConfiguration | Where-Object {$_.IPEnabled -and $_.DefaultIPGateway} | Select-Object -First 1 -ExpandProperty DefaultIPGateway);
            $ip=(Get-CimInstance Win32_NetworkAdapterConfiguration | Where-Object {$_.IPEnabled} | Select-Object -First 1 -ExpandProperty IPAddress);
            $dns=(Get-CimInstance Win32_NetworkAdapterConfiguration | Where-Object {$_.IPEnabled} | Select-Object -First 1 -ExpandProperty DNSServerSearchOrder);
            $ping=if($gw){(Test-Connection -TargetName $gw -Count 1 -Quiet)}else{$false};
            $t=@{ip=$ip;gateway=$gw;dns=$dns;ping_gateway=$ping};
            """
        elif "HARDWARE" in cat or "CPU" in cat:
            telemetry_ps = """
            $cpu=(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average;
            $os=Get-CimInstance Win32_OperatingSystem;
            $ram=[math]::Round((($os.TotalVisibleMemorySize - $os.FreePhysicalMemory)/$os.TotalVisibleMemorySize * 100), 1);
            $disks=(Get-CimInstance Win32_LogicalDisk | Where-Object {$_.DriveType -eq 3} | ForEach-Object {"$($_.DeviceID) Free:$([math]::Round($_.FreeSpace/1GB,1))GB/$([math]::Round($_.Size/1GB,1))GB"}) -join '; ';
            $t=@{cpu_percent=$cpu;ram_percent=$ram;disks=$disks;cpu_name=(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name)};
            """
        elif "MALWARE" in cat or "VIRUS" in cat:
            telemetry_ps = """
            $av=(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | Select-Object -First 1 -ExpandProperty displayName);
            $procs=(Get-Process | Sort-Object CPU -Descending | Select-Object -First 5 | ForEach-Object {"$($_.ProcessName)($([math]::Round($_.CPU,1))s)"}) -join ', ';
            $ports=(Get-NetTCPConnection -State Listen | Select-Object -First 6 | ForEach-Object {"$($_.LocalPort)"}) -join ', ';
            $t=@{antivirus_enabled=$av;top_cpu_procs=$procs;listening_ports=$ports};
            """
        elif "OTROS" in cat or "LOGS" in cat:
            telemetry_ps = """
            $events=(Get-WinEvent -FilterHashtable @{LogName='System';Level=1,2} -MaxEvents 3 | ForEach-Object {"[$($_.TimeCreated.ToString('HH:mm'))] $($_.Message.Substring(0,[math]::Min(60,$_.Message.Length)))"}) -join ' | ';
            $services=(Get-Service | Where-Object {$_.StartType -eq 'Automatic' -and $_.Status -eq 'Stopped'} | Select-Object -First 5 -ExpandProperty Name) -join ', ';
            $t=@{critical_events=$events;stopped_auto_services=$services};
            """
        else:
            # ANALISIS COMPLETO (Consolidated Full Suite)
            telemetry_ps = """
            $cpu=(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average;
            $os=Get-CimInstance Win32_OperatingSystem;
            $ram=[math]::Round((($os.TotalVisibleMemorySize - $os.FreePhysicalMemory)/$os.TotalVisibleMemorySize * 100), 1);
            $ip=(Get-CimInstance Win32_NetworkAdapterConfiguration | Where-Object {$_.IPEnabled} | Select-Object -First 1 -ExpandProperty IPAddress);
            $gw=(Get-CimInstance Win32_NetworkAdapterConfiguration | Where-Object {$_.IPEnabled -and $_.DefaultIPGateway} | Select-Object -First 1 -ExpandProperty DefaultIPGateway);
            $av=(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | Select-Object -First 1 -ExpandProperty displayName);
            $t=@{cpu_percent=$cpu;ram_percent=$ram;ip=$ip;gateway=$gw;antivirus_enabled=$av};
            """

        script = (
            f"$ErrorActionPreference='SilentlyContinue';"
            f"{telemetry_ps.strip()};"
            f"$p=@{{os_type='windows';category='{cat}';hostname=$env:COMPUTERNAME;telemetry=$t}};"
            f"$j=ConvertTo-Json -Compress -Depth 4 $p;"
            f"$b=[System.Text.Encoding]::UTF8.GetBytes($j);"
            f"try{{Invoke-RestMethod -Uri '{endpoint_uri}' -Method Post -Body $b -ContentType 'application/json; charset=utf-8' -TimeoutSec 10}}catch{{"
            f"try{{$wc=New-Object System.Net.WebClient;$wc.Headers.Add('Content-Type','application/json; charset=utf-8');$wc.UploadData('{endpoint_uri}','POST',$b)}}catch{{}}}}"
        )
        return " ".join(line.strip() for line in script.splitlines() if line.strip())

    @classmethod
    def get_powershell_payload(cls, category: str, server_url: str = "http://10.0.0.1:8000") -> str:
        """
        Returns a single-line PowerShell command encoded in Base64 (UTF-16LE) using -EncodedCommand.
        This completely eliminates CLI quotation errors, layout special character corruption,
        and Windows Run dialog (Win+R) parsing issues.
        """
        script = cls.get_powershell_script(category=category, server_url=server_url)
        encoded_bytes = script.encode("utf-16le")
        b64_cmd = base64.b64encode(encoded_bytes).decode("ascii")
        return f"powershell -WindowStyle Hidden -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {b64_cmd}"


class WindowsHIDPlugin(IDiagnosticPlugin):
    """
    Decoupled plugin that injects Windows diagnostic commands via Rubber Ducky
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
        return "diag_win_hid"

    @property
    def name(self) -> str:
        return f"WIN {self._category[:12]} ({self._layout.upper()})"

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

        payload_cmd = WindowsPayloadGenerator.get_powershell_payload(
            category=self._category,
            server_url=self._server_url,
        )

        # Step 2: Inject via USB HID
        if progress_cb:
            progress_cb("Inyectando HID...", 0.3)

        try:
            # Emulate GUI + r to open Windows Run dialog
            self._injector.press_combination("gui", "r")
            time.sleep(0.8)

            # Write PowerShell execution string and press ENTER
            self._injector.write_text(payload_cmd, layout=self._layout)
            time.sleep(0.2)
            self._injector.press_key("enter")

        except Exception as inj_ex:
            logger.error(f"HID injection failed: {inj_ex}")
            if not self._injector.dry_run:
                return DiagnosticResult(
                    plugin_name=self.name,
                    target_identifier=f"Windows ({self._layout.upper()})",
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

        # If in dry-run or simulated without receiving report
        if not report:
            if self._injector.dry_run:
                if progress_cb:
                    progress_cb("Simulando reporte...", 0.85)
                time.sleep(0.5)
                report_data = {
                    "os_type": "WINDOWS",
                    "category": self._category,
                    "hostname": "WIN-MOCK-HOST",
                    "telemetry": {
                        "cpu_percent": 18.5,
                        "ram_percent": 45.2,
                        "ip": "10.0.0.2",
                        "gateway": "10.0.0.1",
                        "antivirus_enabled": "Windows Defender",
                    },
                }
                rep_id = (
                    self._web_server.store_local_report(
                        os_type="WINDOWS",
                        category=self._category,
                        hostname="WIN-MOCK-HOST",
                        telemetry=report_data["telemetry"],
                    )
                    if self._web_server
                    else "mock-win"
                )
                report = type("StoredReportMock", (), {
                    "report_id": rep_id,
                    "hostname": "WIN-MOCK-HOST",
                    "os_type": "WINDOWS",
                    "category": self._category,
                    "telemetry": report_data["telemetry"],
                    "overall_status": "OK",
                    "ai_analysis": None,
                })()

        if not report:
            return DiagnosticResult(
                plugin_name=self.name,
                target_identifier=f"Windows ({self._layout.upper()})",
                status=DiagnosticStatus.FAILED,
                overall_status=Severity.WARNING,
                summary="Timeout esperando reporte",
                details=["No se recibió HTTP POST", "Verifique cable y red RNDIS"],
            )

        # Step 4: Build Metrics & Result
        if progress_cb:
            progress_cb("Procesando métricas...", 0.95)

        metrics: List[DiagnosticMetric] = []
        details: List[str] = [
            f"Host: {getattr(report, 'hostname', 'Windows')[:14]}",
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

        av = t_data.get("antivirus_enabled")
        if av:
            metrics.append(DiagnosticMetric(name="Antivirus", value=str(av)[:15], status=Severity.OK))

        ip = t_data.get("ip")
        if ip:
            ip_str = ip if isinstance(ip, str) else (ip[0] if isinstance(ip, list) and ip else "N/A")
            metrics.append(DiagnosticMetric(name="IP Host", value=str(ip_str), status=Severity.INFO))
            details.append(f"IP:   {str(ip_str)[:14]}")

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        return DiagnosticResult(
            plugin_name=self.name,
            target_identifier=f"Windows ({getattr(report, 'hostname', 'PC')})",
            execution_time_ms=elapsed_ms,
            status=DiagnosticStatus.SUCCESS,
            overall_status=Severity.OK,
            summary=f"Diagnóstico {self._category} OK",
            details=details[:4],
            metrics=metrics,
            raw_output=json.dumps(t_data),
            metadata={"report_id": getattr(report, "report_id", "latest"), "os_type": "WINDOWS"},
        )
