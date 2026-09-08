"""
Tests for Endpoint PC Diagnostic Payloads & Plugins (plugins/endpoints/)
"""

import base64
import json
import time
import unittest
import requests

from core.ducky import DuckyInjector
from core.interfaces import DiagnosticStatus
from core.web_server import REIWebServer
from plugins.endpoints.hid_linux import LinuxHIDPlugin, LinuxPayloadGenerator
from plugins.endpoints.hid_windows import WindowsHIDPlugin, WindowsPayloadGenerator


class TestEndpoints(unittest.TestCase):

    def test_windows_payload_generation(self):
        """Verify Windows PowerShell scripts and Base64 UTF-16LE micro-stager."""
        # 1. Raw script verification
        raw_net = WindowsPayloadGenerator.get_powershell_script("RED / CONEXION")
        self.assertIn("Win32_NetworkAdapterConfiguration", raw_net)
        self.assertIn("Invoke-RestMethod", raw_net)
        self.assertIn("-UseBasicParsing", raw_net)

        raw_hw = WindowsPayloadGenerator.get_powershell_script("HARDWARE / CPU")
        self.assertIn("Win32_Processor", raw_hw)

        raw_malware = WindowsPayloadGenerator.get_powershell_script("ANALISIS MALWARE")
        self.assertIn("AntiVirusProduct", raw_malware)

        # 2. Encoded micro-stager payload command verification
        payload_net = WindowsPayloadGenerator.get_powershell_payload("RED / CONEXION")
        self.assertIn("powershell", payload_net)
        self.assertIn("-EncodedCommand", payload_net)

        # Must be strictly under 250 characters (and under 200 characters)
        self.assertLess(len(payload_net), 250)
        self.assertLess(len(payload_net), 200)

        # Decode Base64 and verify it contains micro-stager downloading from /w/RED
        b64_part = payload_net.split("-EncodedCommand")[-1].strip()
        decoded_stager = base64.b64decode(b64_part).decode("utf-16le")
        self.assertEqual(decoded_stager, "irm -useb http://10.0.0.1:8000/w/RED|iex")

    def test_linux_payload_generation(self):
        """Verify Linux Bash scripts and lightweight micro-stager."""
        # 1. Raw script verification
        raw_net = LinuxPayloadGenerator.get_bash_script("RED / CONEXION")
        self.assertIn("curl", raw_net)
        self.assertIn("ip -4 addr", raw_net)

        raw_hw = LinuxPayloadGenerator.get_bash_script("HARDWARE / CPU")
        self.assertIn("top -bn1", raw_hw)

        # 2. Encoded payload command verification
        payload_net = LinuxPayloadGenerator.get_bash_payload("RED / CONEXION")
        self.assertIn("curl", payload_net)
        self.assertIn("/l/RED", payload_net)

        # Must be strictly under 150 characters
        self.assertLess(len(payload_net), 150)

    def test_windows_plugin_execution_dry_run(self):
        """Verify Windows plugin runs cleanly in dry run with stable ID."""
        injector = DuckyInjector(dry_run=True)
        plugin = WindowsHIDPlugin(category="ANALISIS COMPLETO", keyboard_layout="es", injector=injector)
        self.assertEqual(plugin.id, "diag_win_hid")
        result = plugin.run()
        self.assertEqual(result.status, DiagnosticStatus.SUCCESS)
        self.assertTrue(len(result.metrics) > 0)

    def test_linux_plugin_execution_dry_run(self):
        """Verify Linux plugin runs cleanly in dry run with stable ID."""
        injector = DuckyInjector(dry_run=True)
        plugin = LinuxHIDPlugin(category="ANALISIS COMPLETO", keyboard_layout="es", injector=injector)
        self.assertEqual(plugin.id, "diag_linux_hid")
        result = plugin.run()
        self.assertEqual(result.status, DiagnosticStatus.SUCCESS)
        self.assertTrue(len(result.metrics) > 0)

    def test_windows_all_categories_generation(self):
        """Verify Windows payload generation for all supported categories."""
        categories = ["RED / CONEXION", "HARDWARE / CPU", "ANALISIS MALWARE", "OTROS PROBLEMAS", "ANALISIS COMPLETO"]
        for cat in categories:
            raw = WindowsPayloadGenerator.get_powershell_script(cat)
            self.assertIn("os_type='windows'", raw)
            self.assertIn("Invoke-RestMethod", raw)
            self.assertIn("$ErrorActionPreference='SilentlyContinue'", raw)

            payload = WindowsPayloadGenerator.get_powershell_payload(cat)
            self.assertLess(len(payload), 250)

            b64_part = payload.split("-EncodedCommand")[-1].strip()
            decoded = base64.b64decode(b64_part).decode("utf-16le")
            self.assertIn("irm -useb http://10.0.0.1:8000/w/", decoded)
            self.assertIn("|iex", decoded)

    def test_linux_all_categories_generation(self):
        """Verify Linux payload generation for all supported categories."""
        categories = ["RED / CONEXION", "HARDWARE / CPU", "ANALISIS MALWARE", "OTROS PROBLEMAS", "ANALISIS COMPLETO"]
        for cat in categories:
            raw = LinuxPayloadGenerator.get_bash_script(cat)
            self.assertIn("os_type", raw)
            self.assertIn("curl", raw)

            payload = LinuxPayloadGenerator.get_bash_payload(cat)
            self.assertLess(len(payload), 150)
            self.assertIn("/l/", payload)

    def test_plugin_set_context(self):
        """Verify set_context dynamically updates category, layout and server_url without breaking ID."""
        server = REIWebServer(host="127.0.0.1", port=9991, base_url="http://172.20.0.1:8000")

        win_plugin = WindowsHIDPlugin()
        self.assertEqual(win_plugin.id, "diag_win_hid")
        win_plugin.set_context("RED / CONEXION", "us", web_server=server)
        self.assertEqual(win_plugin.id, "diag_win_hid")
        self.assertIn("RED / CONEXI", win_plugin.name)
        self.assertIn("US", win_plugin.name)
        self.assertEqual(win_plugin._server_url, "http://172.20.0.1:8000")

        linux_plugin = LinuxHIDPlugin()
        self.assertEqual(linux_plugin.id, "diag_linux_hid")
        linux_plugin.set_context("HARDWARE / CPU", "es", web_server=server)
        self.assertEqual(linux_plugin.id, "diag_linux_hid")
        self.assertIn("HARDWARE / C", linux_plugin.name)
        self.assertIn("ES", linux_plugin.name)
        self.assertEqual(linux_plugin._server_url, "http://172.20.0.1:8000")

    def test_web_server_payload_serving(self):
        """Verify WebServer serves plain text scripts at /w/ALL, /l/ALL, /w/RED, etc."""
        server = REIWebServer(host="127.0.0.1", port=8992, base_url="http://127.0.0.1:8992")
        server.start()
        time.sleep(0.5)
        try:
            # 1. Windows endpoints
            resp_w_all = requests.get("http://127.0.0.1:8992/w/ALL", timeout=3)
            self.assertEqual(resp_w_all.status_code, 200)
            self.assertIn("text/plain", resp_w_all.headers.get("Content-Type", ""))
            self.assertIn("os_type='windows'", resp_w_all.text)
            self.assertIn("Invoke-RestMethod", resp_w_all.text)

            resp_w_red = requests.get("http://127.0.0.1:8992/w/RED", timeout=3)
            self.assertEqual(resp_w_red.status_code, 200)
            self.assertIn("Win32_NetworkAdapterConfiguration", resp_w_red.text)

            resp_w_default = requests.get("http://127.0.0.1:8992/w", timeout=3)
            self.assertEqual(resp_w_default.status_code, 200)
            self.assertIn("os_type='windows'", resp_w_default.text)

            # 2. Linux endpoints
            resp_l_all = requests.get("http://127.0.0.1:8992/l/ALL", timeout=3)
            self.assertEqual(resp_l_all.status_code, 200)
            self.assertIn("text/plain", resp_l_all.headers.get("Content-Type", ""))
            self.assertIn("os_type", resp_l_all.text)
            self.assertIn("curl", resp_l_all.text)

            resp_l_hw = requests.get("http://127.0.0.1:8992/l/HARDWARE", timeout=3)
            self.assertEqual(resp_l_hw.status_code, 200)
            self.assertIn("cpu_percent", resp_l_hw.text)

            resp_l_default = requests.get("http://127.0.0.1:8992/l", timeout=3)
            self.assertEqual(resp_l_default.status_code, 200)
            self.assertIn("os_type", resp_l_default.text)
        finally:
            server.stop()

    def test_telemetry_json_deserialization(self):
        """Verify JSON serialization and deserialization of reports from both OS types."""
        # Simulated Windows report
        win_report_json = json.dumps({
            "os_type": "windows",
            "category": "COMPLETO",
            "hostname": "DESKTOP-TEST",
            "telemetry": {
                "cpu_percent": 15.2,
                "ram_percent": 42.0,
                "ip": "10.0.0.2",
                "gateway": "10.0.0.1",
                "antivirus_enabled": "Windows Defender",
            }
        })
        deserialized_win = json.loads(win_report_json)
        self.assertEqual(deserialized_win["os_type"], "windows")
        self.assertEqual(deserialized_win["hostname"], "DESKTOP-TEST")
        self.assertEqual(deserialized_win["telemetry"]["cpu_percent"], 15.2)

        # Simulated Linux report with sanitized strings
        linux_report_json = json.dumps({
            "os_type": "linux",
            "category": "RED",
            "hostname": "linux-client",
            "telemetry": {
                "ip": "10.0.0.2/24",
                "gateway": "10.0.0.1",
                "ping_gateway": True,
            }
        })
        deserialized_linux = json.loads(linux_report_json)
        self.assertEqual(deserialized_linux["os_type"], "linux")
        self.assertTrue(deserialized_linux["telemetry"]["ping_gateway"])

    def test_hardware_and_osi_telemetry_windows(self):
        """Verify Windows script includes hardware and OSI Layer 7-1 extraction."""
        raw_net = WindowsPayloadGenerator.get_powershell_script("RED / CONEXION")
        self.assertIn("Win32_ComputerSystem", raw_net)
        self.assertIn("Win32_BIOS", raw_net)
        self.assertIn("Win32_OperatingSystem", raw_net)
        self.assertIn("l7_application", raw_net)
        self.assertIn("google.com", raw_net)
        self.assertIn("msftconnecttest.com", raw_net)
        self.assertIn("l4_transport", raw_net)
        self.assertIn("l3_network", raw_net)
        self.assertIn("tracert", raw_net)
        self.assertIn("l2_datalink", raw_net)
        self.assertIn("netsh wlan", raw_net)
        self.assertIn("l1_physical", raw_net)

        # Verify dry run plugin extracts hardware and OSI metrics
        injector = DuckyInjector(dry_run=True)
        plugin = WindowsHIDPlugin(category="RED / CONEXION", keyboard_layout="es", injector=injector)
        result = plugin.run()
        self.assertEqual(result.status, DiagnosticStatus.SUCCESS)
        metric_names = [m.name for m in result.metrics]
        self.assertIn("Equipo", metric_names)
        self.assertIn("Gateway", metric_names)
        self.assertIn("DNS Status", metric_names)

    def test_hardware_and_osi_telemetry_linux(self):
        """Verify Linux script includes hardware and OSI Layer 7-1 extraction."""
        raw_net = LinuxPayloadGenerator.get_bash_script("RED / CONEXION")
        self.assertIn("/sys/class/dmi/id", raw_net)
        self.assertIn("/etc/os-release", raw_net)
        self.assertIn("l7_application", raw_net)
        self.assertIn("nameserver", raw_net)
        self.assertIn("generate_204", raw_net)
        self.assertIn("l3_network", raw_net)
        self.assertIn("traceroute", raw_net)
        self.assertIn("l2_datalink", raw_net)
        self.assertIn("l1_physical", raw_net)

        # Verify dry run plugin extracts hardware and OSI metrics
        injector = DuckyInjector(dry_run=True)
        plugin = LinuxHIDPlugin(category="RED / CONEXION", keyboard_layout="es", injector=injector)
        result = plugin.run()
        self.assertEqual(result.status, DiagnosticStatus.SUCCESS)
        metric_names = [m.name for m in result.metrics]
        self.assertIn("Equipo", metric_names)
        self.assertIn("Gateway", metric_names)
        self.assertIn("DNS Status", metric_names)

    def test_mobile_html_renders_hardware_and_osi(self):
        """Verify HTML report includes Hardware Identity and OSI diagnostic tables."""
        server = REIWebServer(host="127.0.0.1", port=8993, base_url="http://127.0.0.1:8993")
        rep_id = server.store_local_report(
            os_type="WINDOWS",
            category="RED",
            hostname="PC-AUDIT",
            telemetry={"ip": "192.168.1.50"},
            hardware={
                "manufacturer": "HP",
                "model": "EliteBook 840 G8",
                "serial": "5CG1234XYZ",
                "os_name": "Microsoft Windows 11 Enterprise",
            },
            osi_network={
                "l7_application": {"dns_ok": True, "dns_servers": ["192.168.1.1"]},
                "l3_network": {"ip": "192.168.1.50", "gateway": "192.168.1.1", "ping_gateway": True},
                "l2_datalink": {"adapter": "Wi-Fi 6", "mac": "00:11:22:33:44:55", "wifi": {"ssid": "CorpNet", "signal_pct": 90}},
                "l1_physical": {"status": "Up"},
            },
        )
        report = server.get_report(rep_id)
        self.assertIsNotNone(report)
        html_out = server._render_mobile_html(report)
        self.assertIn("IDENTIDAD Y HARDWARE DEL HOST", html_out)
        self.assertIn("EliteBook 840 G8", html_out)
        self.assertIn("5CG1234XYZ", html_out)
        self.assertIn("MODELO OSI", html_out)
        self.assertIn("CAPA 7: APLICACIÓN", html_out)
        self.assertIn("CAPA 3: RED", html_out)
        self.assertIn("CAPA 2: ENLACE DE DATOS", html_out)
        self.assertIn("CorpNet", html_out)

    def test_windows_hardware_payload_audit(self):
        """Verify Windows PowerShell script for HARDWARE gathers comprehensive subsystem audit."""
        for cat in ("ANALISIS HARDWARE", "HARDWARE"):
            raw_hw = WindowsPayloadGenerator.get_powershell_script(cat)
            self.assertIn("Win32_BaseBoard", raw_hw)
            self.assertIn("Win32_BIOS", raw_hw)
            self.assertIn("Win32_Processor", raw_hw)
            self.assertIn("Win32_PhysicalMemory", raw_hw)
            self.assertIn("Win32_DiskDrive", raw_hw)
            self.assertIn("MSStorageDriver_FailurePredictStatus", raw_hw)
            self.assertIn("Win32_LogicalDisk", raw_hw)
            self.assertIn("Win32_VideoController", raw_hw)
            self.assertIn("hardware_audit", raw_hw)

    def test_linux_hardware_payload_audit(self):
        """Verify Linux Bash script for HARDWARE gathers comprehensive subsystem audit."""
        for cat in ("ANALISIS HARDWARE", "HARDWARE"):
            raw_hw = LinuxPayloadGenerator.get_bash_script(cat)
            self.assertIn("/sys/class/dmi/id", raw_hw)
            self.assertIn("lsblk", raw_hw)
            self.assertIn("df -h", raw_hw)
            self.assertIn("thermal_zone0", raw_hw)
            self.assertIn("lspci", raw_hw)
            self.assertIn("hardware_audit", raw_hw)

    def test_hardware_plugin_metrics_windows_and_linux(self):
        """Verify dry run execution for HARDWARE extracts dedicated hardware metrics."""
        injector = DuckyInjector(dry_run=True)
        win_plugin = WindowsHIDPlugin(category="ANALISIS HARDWARE", keyboard_layout="es", injector=injector)
        win_res = win_plugin.run()
        self.assertEqual(win_res.status, DiagnosticStatus.SUCCESS)
        win_metric_names = [m.name for m in win_res.metrics]
        self.assertIn("CPU", win_metric_names)
        self.assertIn("RAM", win_metric_names)
        self.assertIn("Salud SMART", win_metric_names)
        self.assertTrue(any("Disco" in m for m in win_metric_names))

        linux_plugin = LinuxHIDPlugin(category="ANALISIS HARDWARE", keyboard_layout="es", injector=injector)
        linux_res = linux_plugin.run()
        self.assertEqual(linux_res.status, DiagnosticStatus.SUCCESS)
        linux_metric_names = [m.name for m in linux_res.metrics]
        self.assertIn("CPU", linux_metric_names)
        self.assertIn("RAM", linux_metric_names)
        self.assertIn("Temp CPU", linux_metric_names)
        self.assertTrue(any("Disco" in m for m in linux_metric_names))

    def test_mobile_html_renders_deep_hardware_audit(self):
        """Verify mobile HTML report displays full hardware audit card with subsystems."""
        server = REIWebServer(host="127.0.0.1", port=8994, base_url="http://127.0.0.1:8994")
        rep_id = server.store_local_report(
            os_type="WINDOWS",
            category="ANALISIS HARDWARE",
            hostname="DESKTOP-RIG",
            telemetry={"cpu_percent": 14.5},
            hardware={"manufacturer": "ASUSTeK", "model": "ROG STRIX B550-F"},
            hardware_audit={
                "system": {
                    "board_mfr": "ASUSTeK COMPUTER INC.",
                    "board_product": "ROG STRIX B550-F GAMING",
                    "bios_version": "3002",
                    "bios_date": "2023-02-23",
                },
                "cpu": {
                    "name": "AMD Ryzen 7 5800X 8-Core Processor",
                    "cores": 8,
                    "threads": 16,
                    "load_pct": 14.5,
                    "current_mhz": 3800,
                },
                "memory": {
                    "total_gb": 32.0,
                    "used_gb": 11.2,
                    "free_gb": 20.8,
                    "usage_pct": 35.0,
                    "slots_used": 2,
                    "slots_total": 4,
                    "dimms": [
                        {"slot": "DIMM_A2", "size_gb": 16.0, "speed_mhz": 3600, "mfr": "Corsair"},
                        {"slot": "DIMM_B2", "size_gb": 16.0, "speed_mhz": 3600, "mfr": "Corsair"},
                    ],
                },
                "storage": {
                    "physical_drives": [
                        {"model": "Samsung SSD 980 PRO 1TB", "size_gb": 1000.2, "bus": "NVMe", "smart_fail": False}
                    ],
                    "volumes": [
                        {"drive": "C:", "fs": "NTFS", "size_gb": 930.5, "free_gb": 650.2, "free_pct": 69.8}
                    ],
                },
                "graphics": [
                    {"name": "NVIDIA GeForce RTX 3080", "driver": "536.23", "vram_mb": 10240, "res": "2560 x 1440"}
                ],
            },
        )
        report = server.get_report(rep_id)
        self.assertIsNotNone(report)
        html_out = server._render_mobile_html(report)
        self.assertIn("AUDITORÍA EXHAUSTIVA DE HARDWARE", html_out)
        self.assertIn("ROG STRIX B550-F GAMING", html_out)
        self.assertIn("Ryzen 7 5800X", html_out)
        self.assertIn("DIMM_A2", html_out)
        self.assertIn("Corsair", html_out)
        self.assertIn("Samsung SSD 980 PRO", html_out)
        self.assertIn("SMART OK", html_out)
        self.assertIn("GeForce RTX 3080", html_out)


if __name__ == "__main__":
    unittest.main()

