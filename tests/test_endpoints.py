"""
Tests for Endpoint PC Diagnostic Payloads & Plugins (plugins/endpoints/)
"""

import base64
import unittest
from core.ducky import DuckyInjector
from core.interfaces import DiagnosticStatus
from plugins.endpoints.hid_linux import LinuxHIDPlugin, LinuxPayloadGenerator
from plugins.endpoints.hid_windows import WindowsHIDPlugin, WindowsPayloadGenerator


class TestEndpoints(unittest.TestCase):

    def test_windows_payload_generation(self):
        """Verify Windows PowerShell scripts and Base64 -EncodedCommand one-liners."""
        # 1. Raw script verification
        raw_net = WindowsPayloadGenerator.get_powershell_script("RED / CONEXION")
        self.assertIn("Win32_NetworkAdapterConfiguration", raw_net)
        self.assertIn("Invoke-RestMethod", raw_net)

        raw_hw = WindowsPayloadGenerator.get_powershell_script("HARDWARE / CPU")
        self.assertIn("Win32_Processor", raw_hw)

        raw_malware = WindowsPayloadGenerator.get_powershell_script("ANALISIS MALWARE")
        self.assertIn("AntiVirusProduct", raw_malware)

        # 2. Encoded payload command verification
        payload_net = WindowsPayloadGenerator.get_powershell_payload("RED / CONEXION")
        self.assertIn("powershell", payload_net)
        self.assertIn("-EncodedCommand", payload_net)

        # Decode Base64 and verify content
        b64_part = payload_net.split("-EncodedCommand")[-1].strip()
        decoded_script = base64.b64decode(b64_part).decode("utf-16le")
        self.assertEqual(raw_net, decoded_script)

    def test_linux_payload_generation(self):
        """Verify Linux Bash scripts and Base64 one-liners."""
        # 1. Raw script verification
        raw_net = LinuxPayloadGenerator.get_bash_script("RED / CONEXION")
        self.assertIn("curl", raw_net)
        self.assertIn("ip -4 addr", raw_net)

        raw_hw = LinuxPayloadGenerator.get_bash_script("HARDWARE / CPU")
        self.assertIn("top -bn1", raw_hw)

        # 2. Encoded payload command verification
        payload_net = LinuxPayloadGenerator.get_bash_payload("RED / CONEXION")
        self.assertIn("echo ", payload_net)
        self.assertIn("base64 -d", payload_net)

        # Extract Base64 and verify content
        b64_part = payload_net.split("echo ")[1].split("|")[0].strip()
        decoded_script = base64.b64decode(b64_part).decode("utf-8")
        self.assertEqual(raw_net, decoded_script)

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
            payload = WindowsPayloadGenerator.get_powershell_payload(cat)
            b64_part = payload.split("-EncodedCommand")[-1].strip()
            decoded = base64.b64decode(b64_part).decode("utf-16le")
            self.assertEqual(raw, decoded)

    def test_linux_all_categories_generation(self):
        """Verify Linux payload generation for all supported categories."""
        categories = ["RED / CONEXION", "HARDWARE / CPU", "ANALISIS MALWARE", "OTROS PROBLEMAS", "ANALISIS COMPLETO"]
        for cat in categories:
            raw = LinuxPayloadGenerator.get_bash_script(cat)
            self.assertIn("os_type", raw)
            self.assertIn("curl", raw)
            payload = LinuxPayloadGenerator.get_bash_payload(cat)
            b64_part = payload.split("echo ")[1].split("|")[0].strip()
            decoded = base64.b64decode(b64_part).decode("utf-8")
            self.assertEqual(raw, decoded)

    def test_plugin_set_context(self):
        """Verify set_context dynamically updates category and layout without breaking ID."""
        win_plugin = WindowsHIDPlugin()
        self.assertEqual(win_plugin.id, "diag_win_hid")
        win_plugin.set_context("RED / CONEXION", "us")
        self.assertEqual(win_plugin.id, "diag_win_hid")
        self.assertIn("RED / CONEXI", win_plugin.name)
        self.assertIn("US", win_plugin.name)

        linux_plugin = LinuxHIDPlugin()
        self.assertEqual(linux_plugin.id, "diag_linux_hid")
        linux_plugin.set_context("HARDWARE / CPU", "es")
        self.assertEqual(linux_plugin.id, "diag_linux_hid")
        self.assertIn("HARDWARE / C", linux_plugin.name)
        self.assertIn("ES", linux_plugin.name)


if __name__ == "__main__":
    unittest.main()
