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
    def get_powershell_script(cls, category: str, server_url: str = "http://10.0.0.1:8000") -> str:
        """
        Returns the raw PowerShell script that gathers telemetry and posts JSON to server_url.
        """
        cat = cls.normalize_category(category)
        endpoint_uri = f"{server_url.rstrip('/')}/api/v1/endpoint/report"

        if cat == "RED":
            telemetry_ps = """
            $cs=Get-CimInstance Win32_ComputerSystem;
            $bios=Get-CimInstance Win32_BIOS;
            $os=Get-CimInstance Win32_OperatingSystem;
            $cpu=Get-CimInstance Win32_Processor | Select-Object -First 1;
            $hw=@{manufacturer=$cs.Manufacturer;model=$cs.Model;serial=$bios.SerialNumber;os_name=$os.Caption;os_version=$os.Version;os_build=$os.BuildNumber;cpu_model=$cpu.Name;ram_total_gb=[math]::Round($cs.TotalPhysicalMemory/1GB,1);ram_free_gb=[math]::Round($os.FreePhysicalMemory/1MB,1)};
            $adapters=Get-CimInstance Win32_NetworkAdapterConfiguration | Where-Object {$_.IPEnabled};
            $nac=($adapters | Where-Object {$_.DefaultIPGateway -and ($_.IPAddress -notlike '10.0.0.*')} | Select-Object -First 1);
            if(-not $nac){$nac=($adapters | Where-Object {$_.DefaultIPGateway} | Select-Object -First 1)};
            if(-not $nac){$nac=($adapters | Select-Object -First 1)};
            $ip=if($nac){$nac.IPAddress[0]}else{$null};
            $mask=if($nac){$nac.IPSubnet[0]}else{$null};
            $gw=if($nac -and $nac.DefaultIPGateway){$nac.DefaultIPGateway[0]}else{$null};
            $dns=if($nac){$nac.DNSServerSearchOrder}else{@()};
            $mac=if($nac){$nac.MACAddress}else{$null};
            $dhcp_on=if($nac){$nac.DHCPEnabled}else{$false};
            $dhcp_srv=if($nac){$nac.DHCPServer}else{$null};
            $adapter_name=if($nac){$nac.Description}else{$null};
            $dns_ok=$false;$dns_ms=0;
            $sw=[System.Diagnostics.Stopwatch]::StartNew();
            try{$r=[System.Net.Dns]::GetHostAddresses('google.com');if($r){$dns_ok=$true}}catch{};
            $sw.Stop();$dns_ms=[int]$sw.ElapsedMilliseconds;
            $http_code=0;$captive=$false;
            try{$req=[System.Net.WebRequest]::Create('http://www.msftconnecttest.com/connecttest.txt');$req.Timeout=2000;$resp=$req.GetResponse();$http_code=[int]$resp.StatusCode;$resp.Close()}catch{if($_.Exception.Response){$http_code=[int]$_.Exception.Response.StatusCode}};
            if($http_code -ne 200 -and $http_code -ne 0){$captive=$true};
            $dom_joined=$cs.PartOfDomain;$dom_name=$cs.Domain;
            $tcp_active=(Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue | Measure-Object).Count;
            $p53_gw=$false;
            if($gw){try{$c53=New-Object System.Net.Sockets.TcpClient;$ar53=$c53.BeginConnect($gw,53,$null,$null);$p53_gw=$ar53.AsyncWaitHandle.WaitOne(800,$false);$c53.Close()}catch{}};
            $p443_ext=$false;
            try{$c443=New-Object System.Net.Sockets.TcpClient;$ar443=$c443.BeginConnect('1.1.1.1',443,$null,$null);$p443_ext=$ar443.AsyncWaitHandle.WaitOne(1000,$false);$c443.Close()}catch{}};
            $gw_ping=if($gw){(Test-Connection -TargetName $gw -Count 2 -Quiet)}else{$false};
            $ext_ping=(Test-Connection -TargetName '8.8.8.8' -Count 2 -Quiet);
            $hops=@();
            try{
                $tr=tracert -d -h 4 -w 300 8.8.8.8;
                foreach($l in ($tr -split "`n")){if($l -match '^\\s*(\\d+)\\s+([\\d\\<\\*]+ ms|\\*)\\s+([\\d\\<\\*]+ ms|\\*)\\s+([\\d\\<\\*]+ ms|\\*)\\s+([\\d\\.]+)'){$hops+=@([int]$matches[1],$matches[5])}}
            }catch{};
            $mtu=try{(Get-NetIPInterface -InterfaceIndex $nac.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).NlMtu}catch{1500};
            $arp_gw=if($gw){$a=arp -a $gw 2>$null | Out-String;if($a -match '([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}'){$matches[0]}else{$null}}else{$null};
            $wifi=@{is_wifi=$false};
            try{
                $wl=netsh wlan show interfaces | Out-String;
                if($wl -match 'SSID\\s*:\\s*(.+)'){$wifi.is_wifi=$true;$wifi.ssid=$matches[1].Trim();if($wl -match 'BSSID\\s*:\\s*(.+)'){$wifi.bssid=$matches[1].Trim()};if($wl -match 'Se[nñ]al|Signal\\s*:\\s*(\\d+)%'){$wifi.signal_pct=[int]$matches[1]};if($wl -match 'Canal|Channel\\s*:\\s*(\\d+)'){$wifi.channel=[int]$matches[1]}}
            }catch{};
            $link_speed=try{(Get-NetAdapter -InterfaceIndex $nac.InterfaceIndex -ErrorAction SilentlyContinue).LinkSpeed}catch{$null};
            $oper_status=try{(Get-NetAdapter -InterfaceIndex $nac.InterfaceIndex -ErrorAction SilentlyContinue).Status}catch{'Up'};
            $osi=@{
                l7_application=@{dns_servers=$dns;dns_ok=$dns_ok;dns_ms=$dns_ms;http_status=$http_code;captive_portal=$captive};
                l6_l5_session=@{domain_joined=$dom_joined;domain_name=$dom_name;active_tcp_conns=$tcp_active};
                l4_transport=@{gateway_port53_open=$p53_gw;internet_port443_open=$p443_ext};
                l3_network=@{ip=$ip;subnet=$mask;gateway=$gw;ping_gateway=$gw_ping;ping_internet=$ext_ping;mtu=$mtu;traceroute_hops=$hops};
                l2_datalink=@{adapter=$adapter_name;mac=$mac;dhcp_enabled=$dhcp_on;dhcp_server=$dhcp_srv;gateway_arp=$arp_gw;wifi=$wifi};
                l1_physical=@{link_speed=$link_speed;status=$oper_status};
            };
            $t=@{ip=$ip;gateway=$gw;dns=$dns;ping_gateway=$gw_ping;ping_internet=$ext_ping;mac=$mac;hardware=$hw;osi_network=$osi};
            """
        elif cat == "HARDWARE":
            telemetry_ps = """
            $cs=Get-CimInstance Win32_ComputerSystem;
            $bios=Get-CimInstance Win32_BIOS;
            $board=Get-CimInstance Win32_BaseBoard | Select-Object -First 1;
            $os=Get-CimInstance Win32_OperatingSystem;
            $cpu=Get-CimInstance Win32_Processor | Select-Object -First 1;
            $cpu_load=(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average;
            $ram_total=[math]::Round($cs.TotalPhysicalMemory/1GB,1);
            $ram_free=[math]::Round($os.FreePhysicalMemory/1MB,1);
            $ram_used=[math]::Round($ram_total - $ram_free,1);
            $ram_pct=[math]::Round((($os.TotalVisibleMemorySize - $os.FreePhysicalMemory)/$os.TotalVisibleMemorySize * 100),1);
            $dimms=Get-CimInstance Win32_PhysicalMemory | ForEach-Object {@{slot=$_.DeviceLocator;size_gb=[math]::Round($_.Capacity/1GB,1);speed_mhz=$_.Speed;mfr=$_.Manufacturer;part=if($_.PartNumber){($_.PartNumber).Trim()}else{""}}};
            $slots_count=try{(Get-CimInstance Win32_PhysicalMemoryArray).MemoryDevices}catch{$dimms.Count};
            $p_disks=Get-CimInstance Win32_DiskDrive | ForEach-Object {
                $sf=$false;
                try{
                    $inst_id=$_.DeviceID.Replace('\\','\\\\');
                    $chk=Get-CimInstance -Namespace root/wmi -ClassName MSStorageDriver_FailurePredictStatus -ErrorAction SilentlyContinue | Where-Object {$_.InstanceName -like "*$inst_id*"};
                    if($chk){$sf=$chk.PredictFailure}
                }catch{};
                @{model=$_.Model;size_gb=[math]::Round($_.Size/1GB,1);bus=$_.InterfaceType;smart_fail=$sf}
            };
            $vols=Get-CimInstance Win32_LogicalDisk | Where-Object {$_.DriveType -eq 3} | ForEach-Object {
                $pct_free=if($_.Size -gt 0){[math]::Round(($_.FreeSpace/$_.Size)*100,1)}else{0};
                @{drive=$_.DeviceID;fs=$_.FileSystem;size_gb=[math]::Round($_.Size/1GB,1);free_gb=[math]::Round($_.FreeSpace/1GB,1);free_pct=$pct_free}
            };
            $disks_summary=($vols | ForEach-Object {"$($_.drive) Libre:$($_.free_gb)GB/$($_.size_gb)GB ($($_.free_pct)%)"}) -join '; ';
            $battery=$null;
            $bat=Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1;
            if($bat){$battery=@{present=$true;charge_pct=$bat.EstimatedChargeRemaining;status=$bat.BatteryStatus}};
            $gpus=Get-CimInstance Win32_VideoController | ForEach-Object {@{name=$_.Name;driver=$_.DriverVersion;vram_mb=if($_.AdapterRAM){[math]::Round($_.AdapterRAM/1MB)}else{$null};res=$_.VideoModeDescription}};
            $hw_audit=@{
                system=@{manufacturer=$cs.Manufacturer;model=$cs.Model;serial=$bios.SerialNumber;board_mfr=$board.Manufacturer;board_product=$board.Product;bios_version=$bios.SMBIOSBIOSVersion;bios_date=$bios.ReleaseDate;os_name=$os.Caption;os_build=$os.BuildNumber};
                cpu=@{name=$cpu.Name;cores=$cpu.NumberOfCores;threads=$cpu.NumberOfLogicalProcessors;max_mhz=$cpu.MaxClockSpeed;current_mhz=$cpu.CurrentClockSpeed;load_pct=$cpu_load};
                memory=@{total_gb=$ram_total;free_gb=$ram_free;used_gb=$ram_used;usage_pct=$ram_pct;slots_total=$slots_count;slots_used=$dimms.Count;dimms=$dimms};
                storage=@{physical_drives=$p_disks;volumes=$vols};
                graphics=$gpus;
                battery=$battery;
            };
            $hw=@{manufacturer=$cs.Manufacturer;model=$cs.Model;serial=$bios.SerialNumber;os_name=$os.Caption;os_version=$os.Version;cpu_model=$cpu.Name;ram_total_gb=$ram_total;ram_free_gb=$ram_free};
            $t=@{cpu_percent=$cpu_load;ram_percent=$ram_pct;disks=$disks_summary;cpu_name=$cpu.Name;hardware=$hw;hardware_audit=$hw_audit};
            """
        elif cat == "MALWARE":
            telemetry_ps = """
            $av=(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | Select-Object -First 1 -ExpandProperty displayName);
            $procs=(Get-Process | Sort-Object CPU -Descending | Select-Object -First 5 | ForEach-Object {"$($_.ProcessName)($([math]::Round($_.CPU,1))s)"}) -join ', ';
            $ports=(Get-NetTCPConnection -State Listen | Select-Object -First 6 | ForEach-Object {"$($_.LocalPort)"}) -join ', ';
            $t=@{antivirus_enabled=$av;top_cpu_procs=$procs;listening_ports=$ports};
            """
        elif cat == "LOGS":
            telemetry_ps = """
            $events=(Get-WinEvent -FilterHashtable @{LogName='System';Level=1,2} -MaxEvents 3 | ForEach-Object {"[$($_.TimeCreated.ToString('HH:mm'))] $($_.Message.Substring(0,[math]::Min(60,$_.Message.Length)))"}) -join ' | ';
            $services=(Get-Service | Where-Object {$_.StartType -eq 'Automatic' -and $_.Status -eq 'Stopped'} | Select-Object -First 5 -ExpandProperty Name) -join ', ';
            $t=@{critical_events=$events;stopped_auto_services=$services};
            """
        else:
            # ANALISIS COMPLETO (Consolidated Full Suite)
            telemetry_ps = """
            $cs=Get-CimInstance Win32_ComputerSystem;
            $bios=Get-CimInstance Win32_BIOS;
            $os=Get-CimInstance Win32_OperatingSystem;
            $cpu=Get-CimInstance Win32_Processor | Select-Object -First 1;
            $hw=@{manufacturer=$cs.Manufacturer;model=$cs.Model;serial=$bios.SerialNumber;os_name=$os.Caption;os_version=$os.Version;os_build=$os.BuildNumber;cpu_model=$cpu.Name;ram_total_gb=[math]::Round($cs.TotalPhysicalMemory/1GB,1);ram_free_gb=[math]::Round($os.FreePhysicalMemory/1MB,1)};
            $cpu_pct=(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average;
            $ram_pct=[math]::Round((($os.TotalVisibleMemorySize - $os.FreePhysicalMemory)/$os.TotalVisibleMemorySize * 100), 1);
            $adapters=Get-CimInstance Win32_NetworkAdapterConfiguration | Where-Object {$_.IPEnabled};
            $nac=($adapters | Where-Object {$_.DefaultIPGateway -and ($_.IPAddress -notlike '10.0.0.*')} | Select-Object -First 1);
            if(-not $nac){$nac=($adapters | Where-Object {$_.DefaultIPGateway} | Select-Object -First 1)};
            if(-not $nac){$nac=($adapters | Select-Object -First 1)};
            $ip=if($nac){$nac.IPAddress[0]}else{$null};
            $mask=if($nac){$nac.IPSubnet[0]}else{$null};
            $gw=if($nac -and $nac.DefaultIPGateway){$nac.DefaultIPGateway[0]}else{$null};
            $dns=if($nac){$nac.DNSServerSearchOrder}else{@()};
            $mac=if($nac){$nac.MACAddress}else{$null};
            $dhcp_on=if($nac){$nac.DHCPEnabled}else{$false};
            $dhcp_srv=if($nac){$nac.DHCPServer}else{$null};
            $adapter_name=if($nac){$nac.Description}else{$null};
            $dns_ok=$false;$dns_ms=0;
            $sw=[System.Diagnostics.Stopwatch]::StartNew();
            try{$r=[System.Net.Dns]::GetHostAddresses('google.com');if($r){$dns_ok=$true}}catch{};
            $sw.Stop();$dns_ms=[int]$sw.ElapsedMilliseconds;
            $http_code=0;$captive=$false;
            try{$req=[System.Net.WebRequest]::Create('http://www.msftconnecttest.com/connecttest.txt');$req.Timeout=2000;$resp=$req.GetResponse();$http_code=[int]$resp.StatusCode;$resp.Close()}catch{if($_.Exception.Response){$http_code=[int]$_.Exception.Response.StatusCode}};
            if($http_code -ne 200 -and $http_code -ne 0){$captive=$true};
            $gw_ping=if($gw){(Test-Connection -TargetName $gw -Count 2 -Quiet)}else{$false};
            $ext_ping=(Test-Connection -TargetName '8.8.8.8' -Count 2 -Quiet);
            $av=(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | Select-Object -First 1 -ExpandProperty displayName);
            $osi=@{
                l7_application=@{dns_servers=$dns;dns_ok=$dns_ok;dns_ms=$dns_ms;http_status=$http_code;captive_portal=$captive};
                l3_network=@{ip=$ip;subnet=$mask;gateway=$gw;ping_gateway=$gw_ping;ping_internet=$ext_ping};
                l2_datalink=@{adapter=$adapter_name;mac=$mac;dhcp_enabled=$dhcp_on;dhcp_server=$dhcp_srv};
            };
            $t=@{cpu_percent=$cpu_pct;ram_percent=$ram_pct;ip=$ip;gateway=$gw;antivirus_enabled=$av;ping_gateway=$gw_ping;hardware=$hw;osi_network=$osi};
            """

        script = (
            f"$ErrorActionPreference='SilentlyContinue';"
            f"$hw=$null;$osi=$null;$hw_audit=$null;"
            f"{telemetry_ps.strip()};"
            f"$p=@{{os_type='windows';category='{cat}';hostname=$env:COMPUTERNAME;hardware=$hw;hardware_audit=$hw_audit;osi_network=$osi;telemetry=$t}};"
            f"$j=ConvertTo-Json -Compress -Depth 5 $p;"
            f"$b=[System.Text.Encoding]::UTF8.GetBytes($j);"
            f"try{{Invoke-RestMethod -UseBasicParsing -Uri '{endpoint_uri}' -Method Post -Body $b -ContentType 'application/json; charset=utf-8' -TimeoutSec 10}}catch{{"
            f"try{{$wc=New-Object System.Net.WebClient;$wc.Headers.Add('Content-Type','application/json; charset=utf-8');$wc.UploadData('{endpoint_uri}','POST',$b)}}catch{{}}}}"
        )
        return " ".join(line.strip() for line in script.splitlines() if line.strip())

    @classmethod
    def get_powershell_payload(cls, category: str, server_url: str = "http://10.0.0.1:8000") -> str:
        """
        Generates a compact Base64 UTF-16LE micro-stager (<200 chars).
        Downloads and executes the full PowerShell telemetry script from REI's local web server.
        Fits easily inside the Windows Run dialog (Win+R) buffer (<260 chars) and types in ~5 seconds.
        """
        cat = cls.normalize_category(category)
        stager = f"irm -useb {server_url.rstrip('/')}/w/{cat}|iex"
        encoded_bytes = stager.encode("utf-16le")
        b64_cmd = base64.b64encode(encoded_bytes).decode("ascii")
        return f"powershell -w h -NoProfile -NonInteractive -ep bypass -EncodedCommand {b64_cmd}"


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
                target_identifier=f"Windows ({self._layout.upper()})",
                status=DiagnosticStatus.FAILED,
                overall_status=Severity.WARNING,
                summary=not_ready_details[0] if not_ready_details else "USB no listo",
                details=not_ready_details,
            )

        # Step 1: Prepare Payload
        if progress_cb:
            progress_cb("Generando payload...", 0.1)

        payload_cmd = WindowsPayloadGenerator.get_powershell_payload(
            category=self._category,
            server_url=self._server_url,
        )

        # Capture watermark before HID typing to eliminate race conditions
        watermark = self._web_server.get_report_watermark() if self._web_server else 0

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
                    target_identifier=f"Windows ({self._layout.upper()})",
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
                    "hardware": {
                        "manufacturer": "Dell Inc.",
                        "model": "OptiPlex 7090",
                        "serial": "8X7Y6Z1",
                        "os_name": "Microsoft Windows 11 Pro",
                        "os_version": "10.0.22631",
                        "cpu_model": "11th Gen Intel(R) Core(TM) i7-11700 @ 2.50GHz",
                        "ram_total_gb": 16.0,
                        "ram_free_gb": 9.4,
                    },
                    "osi_network": {
                        "l7_application": {
                            "dns_servers": ["10.0.0.1", "8.8.8.8"],
                            "dns_ok": True,
                            "dns_ms": 14,
                            "http_status": 200,
                            "captive_portal": False,
                        },
                        "l6_l5_session": {
                            "domain_joined": True,
                            "domain_name": "CORP.LOCAL",
                            "active_tcp_conns": 24,
                        },
                        "l4_transport": {
                            "gateway_port53_open": True,
                            "internet_port443_open": True,
                        },
                        "l3_network": {
                            "ip": "192.168.1.145",
                            "subnet": "255.255.255.0",
                            "gateway": "192.168.1.1",
                            "ping_gateway": True,
                            "ping_internet": True,
                            "mtu": 1500,
                            "traceroute_hops": [[1, "192.168.1.1"], [2, "10.50.0.1"], [3, "8.8.8.8"]],
                        },
                        "l2_datalink": {
                            "adapter": "Intel Ethernet Connection I219-LM",
                            "mac": "00:1A:2B:3C:4D:5E",
                            "dhcp_enabled": True,
                            "dhcp_server": "192.168.1.1",
                            "gateway_arp": "E0:5A:02:11:22:33",
                            "wifi": {"is_wifi": False},
                        },
                        "l1_physical": {
                            "link_speed": "1 Gbps",
                            "status": "Up",
                        },
                    },
                    "hardware_audit": {
                        "system": {
                            "manufacturer": "Dell Inc.",
                            "model": "OptiPlex 7090",
                            "serial": "8X7Y6Z1",
                            "board_mfr": "Dell Inc.",
                            "board_product": "0K375V",
                            "bios_version": "1.14.0",
                            "bios_date": "2023-04-12",
                            "os_name": "Microsoft Windows 11 Pro",
                            "os_build": "22631",
                        },
                        "cpu": {
                            "name": "11th Gen Intel(R) Core(TM) i7-11700 @ 2.50GHz",
                            "cores": 8,
                            "threads": 16,
                            "max_mhz": 4900,
                            "current_mhz": 2500,
                            "load_pct": 18.5,
                        },
                        "memory": {
                            "total_gb": 16.0,
                            "free_gb": 9.4,
                            "used_gb": 6.6,
                            "usage_pct": 41.2,
                            "slots_total": 4,
                            "slots_used": 2,
                            "dimms": [
                                {"slot": "DIMM1", "size_gb": 8.0, "speed_mhz": 3200, "mfr": "SK Hynix", "part": "HMA81GU6CJR8N-XN"},
                                {"slot": "DIMM2", "size_gb": 8.0, "speed_mhz": 3200, "mfr": "SK Hynix", "part": "HMA81GU6CJR8N-XN"},
                            ],
                        },
                        "storage": {
                            "physical_drives": [
                                {"model": "NVMe KIOXIA 512GB", "size_gb": 476.9, "bus": "NVMe", "smart_fail": False}
                            ],
                            "volumes": [
                                {"drive": "C:", "fs": "NTFS", "size_gb": 476.1, "free_gb": 312.4, "free_pct": 65.6}
                            ],
                        },
                        "graphics": [
                            {"name": "Intel(R) UHD Graphics 750", "driver": "31.0.101.4575", "vram_mb": 1024, "res": "1920 x 1080 x 32-bit"}
                        ],
                        "battery": None,
                    },
                    "telemetry": {
                        "cpu_percent": 18.5,
                        "ram_percent": 45.2,
                        "ip": "192.168.1.145",
                        "gateway": "192.168.1.1",
                        "mac": "00:1A:2B:3C:4D:5E",
                        "ping_gateway": True,
                        "ping_internet": True,
                        "antivirus_enabled": "Windows Defender",
                    },
                }
                rep_id = (
                    self._web_server.store_local_report(
                        os_type="WINDOWS",
                        category=self._category,
                        hostname="WIN-MOCK-HOST",
                        telemetry=report_data["telemetry"],
                        hardware=report_data["hardware"],
                        hardware_audit=report_data["hardware_audit"],
                        osi_network=report_data["osi_network"],
                    )
                    if self._web_server
                    else "mock-win"
                )
                report = type("StoredReportMock", (), {
                    "report_id": rep_id,
                    "hostname": "WIN-MOCK-HOST",
                    "os_type": "WINDOWS",
                    "category": self._category,
                    "hardware": report_data["hardware"],
                    "hardware_audit": report_data["hardware_audit"],
                    "osi_network": report_data["osi_network"],
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

        hw_data = getattr(report, "hardware", {}) or getattr(report, "telemetry", {}).get("hardware", {})
        if hw_data:
            mfr = hw_data.get("manufacturer") or ""
            model = hw_data.get("model") or "PC"
            hw_str = f"{mfr} {model}".strip()[:18]
            metrics.append(DiagnosticMetric(name="Equipo", value=hw_str, status=Severity.INFO))
            details.append(f"Eq:   {hw_str[:14]}")

        osi_data = getattr(report, "osi_network", {}) or getattr(report, "telemetry", {}).get("osi_network", {})
        hw_audit = getattr(report, "hardware_audit", {}) or getattr(report, "telemetry", {}).get("hardware_audit", {})
        t_data = getattr(report, "telemetry", {})
        overall_severity = Severity.OK

        # Dedicated Hardware category processing
        if (self._category == "HARDWARE" or "HARDWARE" in self._category) and hw_audit:
            cpu_info = hw_audit.get("cpu", {})
            c_load = cpu_info.get("load_pct") or t_data.get("cpu_percent") or 0
            c_cores = cpu_info.get("cores")
            c_threads = cpu_info.get("threads")
            c_core_str = f" ({c_cores}C/{c_threads}T)" if c_cores and c_threads else ""
            c_sev = Severity.CRITICAL if float(c_load) > 90 else (Severity.WARNING if float(c_load) > 75 else Severity.OK)
            if c_sev != Severity.OK and overall_severity != Severity.CRITICAL:
                overall_severity = c_sev
            metrics.append(DiagnosticMetric(name="CPU", value=f"{c_load}%{c_core_str}", status=c_sev))

            mem_info = hw_audit.get("memory", {})
            m_tot = mem_info.get("total_gb") or hw_data.get("ram_total_gb") or 0
            m_used = mem_info.get("used_gb") or 0
            m_pct = mem_info.get("usage_pct") or t_data.get("ram_percent") or 0
            m_sev = Severity.CRITICAL if float(m_pct) > 90 else (Severity.WARNING if float(m_pct) > 80 else Severity.OK)
            if m_sev != Severity.OK and overall_severity != Severity.CRITICAL:
                overall_severity = m_sev
            metrics.append(DiagnosticMetric(name="RAM", value=f"{m_used}/{m_tot} GB ({m_pct}%)", status=m_sev))

            storage_info = hw_audit.get("storage", {})
            p_drives = storage_info.get("physical_drives", [])
            smart_failed = any(d.get("smart_fail") is True for d in p_drives if isinstance(d, dict))
            if smart_failed:
                metrics.append(DiagnosticMetric(name="Salud SMART", value="FALLO DETECTADO", status=Severity.CRITICAL))
                overall_severity = Severity.CRITICAL
            elif p_drives:
                metrics.append(DiagnosticMetric(name="Salud SMART", value="Correcto (OK)", status=Severity.OK))

            vols = storage_info.get("volumes", [])
            for v in vols:
                drv = v.get("drive", "Disco")
                f_gb = v.get("free_gb", 0)
                f_pct = v.get("free_pct", 100)
                d_sev = Severity.CRITICAL if f_pct < 5 else (Severity.WARNING if f_pct < 10 else Severity.OK)
                if d_sev != Severity.OK and overall_severity != Severity.CRITICAL:
                    overall_severity = d_sev
                metrics.append(DiagnosticMetric(name=f"Disco {drv}", value=f"{f_gb}GB lib ({f_pct}%)", status=d_sev))

            bat_info = hw_audit.get("battery")
            if bat_info and isinstance(bat_info, dict) and bat_info.get("present"):
                ch_pct = bat_info.get("charge_pct", 0)
                metrics.append(DiagnosticMetric(name="Batería", value=f"{ch_pct}%", status=Severity.OK))

            details = [
                f"Host: {getattr(report, 'hostname', 'Windows')[:14]}",
                f"CPU: {c_load}%{c_core_str}"[:14],
                f"RAM: {m_used}/{m_tot}GB ({m_pct}%)"[:14],
                f"SMR: {'ALERTA' if smart_failed else 'OK'} DSK:{len(vols)}"[:14],
            ]
        else:
            # General / Network / Malware metrics
            cpu = t_data.get("cpu_percent")
            if cpu is not None:
                c_val = f"{cpu}%"
                c_sev = Severity.CRITICAL if float(cpu) > 90 else (Severity.WARNING if float(cpu) > 75 else Severity.OK)
                metrics.append(DiagnosticMetric(name="Uso CPU", value=c_val, status=c_sev))
                if len(details) < 4:
                    details.append(f"CPU:  {c_val}")

            ram = t_data.get("ram_percent")
            if ram is not None:
                r_val = f"{ram}%"
                r_sev = Severity.CRITICAL if float(ram) > 90 else Severity.OK
                metrics.append(DiagnosticMetric(name="Uso RAM", value=r_val, status=r_sev))

            # Network OSI Metrics
            l3 = osi_data.get("l3_network", {}) if isinstance(osi_data, dict) else {}
            l7 = osi_data.get("l7_application", {}) if isinstance(osi_data, dict) else {}
            l2 = osi_data.get("l2_datalink", {}) if isinstance(osi_data, dict) else {}

            ip_val = l3.get("ip") or t_data.get("ip")
            if ip_val:
                ip_str = ip_val if isinstance(ip_val, str) else (ip_val[0] if isinstance(ip_val, list) and ip_val else "N/A")
                metrics.append(DiagnosticMetric(name="IP Host", value=str(ip_str), status=Severity.INFO))
                if len(details) < 4:
                    details.append(f"IP:   {str(ip_str)[:14]}")

            gw_val = l3.get("gateway") or t_data.get("gateway")
            gw_ping = l3.get("ping_gateway") if "ping_gateway" in l3 else t_data.get("ping_gateway")
            if gw_val:
                gw_status = Severity.OK if gw_ping else Severity.CRITICAL
                metrics.append(DiagnosticMetric(name="Gateway", value=f"{gw_val} ({'OK' if gw_ping else 'FAIL'})", status=gw_status))

            mac_val = l2.get("mac") or t_data.get("mac")
            if mac_val:
                metrics.append(DiagnosticMetric(name="MAC", value=str(mac_val), status=Severity.INFO))

            dns_ok = l7.get("dns_ok")
            if dns_ok is not None:
                dns_status = Severity.OK if dns_ok else Severity.CRITICAL
                metrics.append(DiagnosticMetric(name="DNS Status", value="Resuelto" if dns_ok else "Fallo", status=dns_status))

            av = t_data.get("antivirus_enabled")
            if av:
                metrics.append(DiagnosticMetric(name="Antivirus", value=str(av)[:15], status=Severity.OK))

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        return DiagnosticResult(
            plugin_name=self.name,
            target_identifier=f"Windows ({getattr(report, 'hostname', 'PC')})",
            execution_time_ms=elapsed_ms,
            status=DiagnosticStatus.SUCCESS,
            overall_status=overall_severity,
            summary=f"Diagnóstico {self._category} OK" if overall_severity != Severity.CRITICAL else f"Diagnóstico {self._category} CON ALERTAS",
            details=details[:4],
            metrics=metrics,
            raw_output=json.dumps(t_data),
            metadata={"report_id": getattr(report, "report_id", "latest"), "os_type": "WINDOWS"},
        )
