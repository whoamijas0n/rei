"""
REI - USB CDC-ACM Serial Receiver
Listens on /dev/ttyGS0 for diagnostic telemetry sent by injected host scripts.
Assembles incoming frames, validates against EndpointDiagnosticData schema,
and provides simulated endpoints for testing and dry-run modes.
"""

import json
import logging
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional

try:
    import serial
except ImportError:
    serial = None

from .interfaces import EndpointDiagnosticData, StorageDriveInfo

logger = logging.getLogger("REI.Core.SerialReceiver")

BEGIN_MARKER = "<<<REI_BEGIN>>>"
END_MARKER = "<<<REI_END>>>"


class USBSerialReceiver:
    """
    Listens on the Linux USB Gadget CDC-ACM serial port (/dev/ttyGS0).
    Receives JSON telemetry emitted by endpoints and converts it into EndpointDiagnosticData.
    """

    DEFAULT_PORT = "/dev/ttyGS0"
    DEFAULT_BAUDRATE = 115200

    def __init__(self, port: Optional[str] = None, baudrate: int = DEFAULT_BAUDRATE):
        self.port = port or self.DEFAULT_PORT
        self.baudrate = baudrate
        self._is_simulated = (
            os.getenv("REI_DRY_RUN") == "1" or
            not os.path.exists(self.port) or
            serial is None
        )
        if self._is_simulated:
            logger.info(f"Serial Receiver initialized in SIMULATION mode (port '{self.port}' unavailable or DRY_RUN).")
        else:
            logger.info(f"Serial Receiver initialized on hardware port: {self.port} ({self.baudrate} baud)")

    @property
    def is_simulated(self) -> bool:
        return self._is_simulated

    def wait_for_endpoint_data(
        self,
        timeout: float = 15.0,
        expected_os: str = "windows",
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> Optional[EndpointDiagnosticData]:
        """
        Polls the CDC-ACM port until a complete diagnostic frame is assembled or timeout is reached.
        In simulation mode, produces realistic simulated telemetry after simulating execution time.
        """
        if self._is_simulated:
            return self._simulate_reception(timeout=timeout, expected_os=expected_os, progress_callback=progress_callback)

        return self._hardware_listen(timeout=timeout, progress_callback=progress_callback)

    def _hardware_listen(
        self,
        timeout: float = 15.0,
        progress_callback: Optional[Callable[[str, float], None]] = None,
    ) -> Optional[EndpointDiagnosticData]:
        """Reads frames from physical serial port /dev/ttyGS0."""
        start_time = time.monotonic()
        buffer = ""

        try:
            ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                timeout=0.5,
                write_timeout=0.5
            )
            # Flush any old unread garbage
            ser.reset_input_buffer()
        except Exception as ex:
            logger.error(f"Cannot open serial port {self.port}: {ex}")
            return None

        try:
            while (time.monotonic() - start_time) < timeout:
                elapsed = time.monotonic() - start_time
                pct = min(0.95, 0.20 + (elapsed / timeout) * 0.70)
                if progress_callback:
                    progress_callback(f"Esperando datos ({int(timeout - elapsed)}s)...", pct)

                try:
                    raw = ser.read(ser.in_waiting or 1)
                    if raw:
                        chunk = raw.decode("utf-8", errors="replace")
                        buffer += chunk

                        # Check for framed payload
                        if BEGIN_MARKER in buffer and END_MARKER in buffer:
                            start_idx = buffer.find(BEGIN_MARKER) + len(BEGIN_MARKER)
                            end_idx = buffer.find(END_MARKER, start_idx)
                            json_str = buffer[start_idx:end_idx].strip()
                            data = self._parse_json_payload(json_str)
                            if data:
                                return data

                        # Direct JSON detection if no markers
                        elif buffer.strip().startswith("{") and buffer.strip().endswith("}"):
                            data = self._parse_json_payload(buffer.strip())
                            if data:
                                return data
                except Exception as read_err:
                    logger.debug(f"Serial read error: {read_err}")

                time.sleep(0.05)

        finally:
            try:
                ser.close()
            except Exception:
                pass

        logger.warning(f"Timeout ({timeout}s) waiting for endpoint response on {self.port}.")
        return None

    def _simulate_reception(
        self,
        timeout: float,
        expected_os: str,
        progress_callback: Optional[Callable[[str, float], None]],
    ) -> EndpointDiagnosticData:
        """Simulates endpoint payload transmission for testing and development."""
        steps = [
            ("Inyectando payload...", 0.20, 0.4),
            ("Endpoint procesando WMI/CIM...", 0.50, 0.5),
            ("Transfiriendo por CDC-ACM...", 0.85, 0.3),
            ("Validando datos...", 0.95, 0.2),
        ]
        is_dry_run = os.getenv("REI_DRY_RUN") == "1"
        for msg, pct, delay in steps:
            if progress_callback:
                progress_callback(msg, pct)
            time.sleep(0.005 if is_dry_run else delay)

        if expected_os.lower() == "windows":
            return EndpointDiagnosticData(
                os_type="windows",
                os_version="Microsoft Windows 11 Pro 10.0.22631",
                hostname="DESKTOP-REI01",
                uptime_seconds=345600.0,  # 4 days
                cpu_model="11th Gen Intel(R) Core(TM) i7-1185G7 @ 3.00GHz",
                cpu_load_pct=28.5,
                ram_total_mb=16284.0,
                ram_used_mb=11400.0,
                ram_free_mb=4884.0,
                drives=[
                    StorageDriveInfo(
                        device="C:",
                        size_gb=476.2,
                        free_gb=118.5,
                        health_status="OK",
                        smart_alerts=[]
                    ),
                    StorageDriveInfo(
                        device="D:",
                        size_gb=931.5,
                        free_gb=412.0,
                        health_status="OK",
                        smart_alerts=[]
                    )
                ],
                critical_events=[
                    "2026-09-02 14:15 [ID 41] Kernel-Power: Sistema reiniciado inesperadamente"
                ],
                network_adapters=[
                    {"name": "Intel(R) Wi-Fi 6 AX201", "ip": "192.168.1.145"},
                    {"name": "Realtek USB GbE", "ip": "172.16.0.2"}
                ],
                raw_payload={"mock": True}
            )
        else:
            return EndpointDiagnosticData(
                os_type="linux",
                os_version="Ubuntu 22.04.4 LTS (Jammy Jellyfish)",
                hostname="ubuntu-endpoint",
                uptime_seconds=864000.0,  # 10 days
                cpu_model="AMD Ryzen 7 5800H with Radeon Graphics",
                cpu_load_pct=14.2,
                ram_total_mb=31980.0,
                ram_used_mb=8540.0,
                ram_free_mb=23440.0,
                drives=[
                    StorageDriveInfo(
                        device="/dev/nvme0n1p2",
                        size_gb=468.0,
                        free_gb=285.4,
                        health_status="OK",
                        smart_alerts=[]
                    )
                ],
                critical_events=[],
                network_adapters=[
                    {"name": "wlan0", "ip": "192.168.1.189"},
                    {"name": "usb0", "ip": "172.16.0.2"}
                ],
                raw_payload={"mock": True}
            )

    def _parse_json_payload(self, json_str: str) -> Optional[EndpointDiagnosticData]:
        """Parses and validates raw JSON string into EndpointDiagnosticData."""
        try:
            d = json.loads(json_str)
            drives = []
            for drv in d.get("drives", []):
                drives.append(StorageDriveInfo(
                    device=drv.get("device", "Unknown"),
                    size_gb=float(drv.get("size_gb", 0.0)),
                    free_gb=float(drv.get("free_gb", 0.0)),
                    health_status=drv.get("health_status", "OK"),
                    smart_alerts=drv.get("smart_alerts", [])
                ))

            return EndpointDiagnosticData(
                os_type=d.get("os_type", "unknown"),
                os_version=d.get("os_version", ""),
                hostname=d.get("hostname", ""),
                uptime_seconds=float(d.get("uptime_seconds", 0.0)),
                cpu_model=d.get("cpu_model", ""),
                cpu_load_pct=float(d.get("cpu_load_pct", 0.0)),
                ram_total_mb=float(d.get("ram_total_mb", 0.0)),
                ram_used_mb=float(d.get("ram_used_mb", 0.0)),
                ram_free_mb=float(d.get("ram_free_mb", 0.0)),
                drives=drives,
                critical_events=d.get("critical_events", []),
                network_adapters=d.get("network_adapters", []),
                raw_payload=d,
                timestamp=time.time()
            )
        except Exception as ex:
            logger.error(f"Failed to parse endpoint JSON payload: {ex}\nRaw payload: {json_str[:200]}")
            return None


def get_windows_collector_script() -> str:
    """Returns the compact, robust PowerShell script executed in-memory on the Windows endpoint."""
    return """
$ErrorActionPreference = 'SilentlyContinue'
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$disks = Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3"
$smart = Get-CimInstance -Namespace root\\wmi -ClassName MSStorageDriver_FailurePredictStatus

$drivesList = @()
foreach ($d in $disks) {
    $smartFail = $false
    if ($smart -and ($smart | Where-Object { $_.PredictFailure -eq $true })) { $smartFail = $true }
    $drivesList += @{
        device = $d.DeviceID
        size_gb = [math]::Round($d.Size / 1GB, 1)
        free_gb = [math]::Round($d.FreeSpace / 1GB, 1)
        health_status = if ($smartFail) { "PREDICT_FAILURE" } else { "OK" }
        smart_alerts = if ($smartFail) { @("SMART Failure Predicted") } else { @() }
    }
}

$events = Get-WinEvent -FilterHashtable @{LogName='System'; Id=41,1001,6008; StartTime=(Get-Date).AddDays(-7)} -MaxEvents 5 | ForEach-Object { "$($_.TimeCreated.ToString('yyyy-MM-dd HH:mm')) [ID $($_.Id)] $($_.Message.Split([char]10)[0])" }
if (-not $events) { $events = @() }

$adapters = Get-CimInstance Win32_NetworkAdapterConfiguration -Filter "IPEnabled=TRUE" | ForEach-Object {
    @{
        name = $_.Description
        ip = ($_.IPAddress | Where-Object { $_ -notlike '*:*' } | Select-Object -First 1)
    }
}

$data = @{
    os_type = "windows"
    os_version = "$($os.Caption) $($os.Version)"
    hostname = $env:COMPUTERNAME
    uptime_seconds = [math]::Round(((Get-Date) - $os.LastBootUpTime).TotalSeconds, 0)
    cpu_model = $cpu.Name
    cpu_load_pct = [double]$cpu.LoadPercentage
    ram_total_mb = [math]::Round($os.TotalVisibleMemorySize / 1024, 0)
    ram_free_mb = [math]::Round($os.FreePhysicalMemory / 1024, 0)
    ram_used_mb = [math]::Round(($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) / 1024, 0)
    drives = $drivesList
    critical_events = @($events)
    network_adapters = @($adapters)
}

$json = $data | ConvertTo-Json -Compress -Depth 4
$ports = [System.IO.Ports.SerialPort]::getportnames()
foreach ($p in $ports) {
    try {
        $sp = New-Object System.IO.Ports.SerialPort $p, 115200, None, 8, One
        $sp.WriteTimeout = 1000
        $sp.Open()
        $sp.WriteLine("<<<REI_BEGIN>>>" + $json + "<<<REI_END>>>")
        $sp.Close()
        break
    } catch {}
}
""".strip()


def get_linux_collector_script() -> str:
    """Returns the compact, robust Bash script executed in-memory on the Linux endpoint."""
    return """
OS_VER=$(grep PRETTY_NAME /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"' || uname -sr)
HOST=$(hostname 2>/dev/null || echo "linux-host")
UPTIME_SEC=$(awk '{print int($1)}' /proc/uptime 2>/dev/null || echo 0)
CPU_MODEL=$(grep -m1 "model name" /proc/cpuinfo 2>/dev/null | cut -d: -f2 | sed 's/^[ \t]*//' || echo "CPU")
RAM_TOTAL=$(free -m 2>/dev/null | awk '/^Mem:/{print $2}' || echo 0)
RAM_USED=$(free -m 2>/dev/null | awk '/^Mem:/{print $3}' || echo 0)
RAM_FREE=$(free -m 2>/dev/null | awk '/^Mem:/{print $4}' || echo 0)

DRIVES_JSON=$(df -BM 2>/dev/null | awk 'NR>1 && $1 ~ /^\\/dev\\// {printf "{\\\"device\\\":\\\"%s\\\",\\\"size_gb\\\":%.1f,\\\"free_gb\\\":%.1f,\\\"health_status\\\":\\\"OK\\\",\\\"smart_alerts\\\":[]},", $1, $2/1024, $4/1024}' | sed 's/,$//')

FAILED_UNITS=$(systemctl --failed --no-legend 2>/dev/null | awk '{print $2}' | tr '\\n' ',' | sed 's/,$//')
EVENTS=()
if [ -n "$FAILED_UNITS" ]; then
    EVENTS+=("Failed systemd units: $FAILED_UNITS")
fi
DMESG_ERRS=$(dmesg -l err,crit,alert,emerg 2>/dev/null | tail -n 3 | tr '\\n' '|' | sed 's/"/\\\\"/g')
if [ -n "$DMESG_ERRS" ]; then
    EVENTS+=("Kernel errors: $DMESG_ERRS")
fi

EVENTS_JSON=$(printf '"%s",' "${EVENTS[@]}" | sed 's/,$//')

JSON="{\\\"os_type\\\":\\\"linux\\\",\\\"os_version\\\":\\\"$OS_VER\\\",\\\"hostname\\\":\\\"$HOST\\\",\\\"uptime_seconds\\\":$UPTIME_SEC,\\\"cpu_model\\\":\\\"$CPU_MODEL\\\",\\\"cpu_load_pct\\\":15.0,\\\"ram_total_mb\\\":$RAM_TOTAL,\\\"ram_used_mb\\\":$RAM_USED,\\\"ram_free_mb\\\":$RAM_FREE,\\\"drives\\\":[$DRIVES_JSON],\\\"critical_events\\\":[$EVENTS_JSON],\\\"network_adapters\\\":[]}"

for p in /dev/ttyACM* /dev/serial/by-id/* /dev/ttyUSB*; do
    if [ -w "$p" ]; then
        echo "<<<REI_BEGIN>>>$JSON<<<REI_END>>>" > "$p" 2>/dev/null
        break
    fi
done
""".strip()
