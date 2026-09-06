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
        self.assertEqual(decoded_stager, "irm http://10.0.0.1:8000/w/RED|iex")

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
            self.assertIn("irm http://10.0.0.1:8000/w/", decoded)
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


if __name__ == "__main__":
    unittest.main()

