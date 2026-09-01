"""
Tests for Endpoint PC Diagnostic Payloads & Plugins (plugins/endpoints/)
"""

import unittest
from core.ducky import DuckyInjector
from core.interfaces import DiagnosticStatus
from plugins.endpoints.hid_linux import LinuxHIDPlugin, LinuxPayloadGenerator
from plugins.endpoints.hid_windows import WindowsHIDPlugin, WindowsPayloadGenerator


class TestEndpoints(unittest.TestCase):

    def test_windows_payload_generation(self):
        """Verify Windows PowerShell one-liners are generated properly."""
        payload_net = WindowsPayloadGenerator.get_powershell_payload("RED / CONEXION")
        self.assertIn("powershell", payload_net)
        self.assertIn("Get-NetIPAddress", payload_net)
        self.assertIn("Invoke-RestMethod", payload_net)

        payload_hw = WindowsPayloadGenerator.get_powershell_payload("HARDWARE / CPU")
        self.assertIn("Win32_Processor", payload_hw)

        payload_malware = WindowsPayloadGenerator.get_powershell_payload("ANALISIS MALWARE")
        self.assertIn("AntiVirusProduct", payload_malware)

    def test_linux_payload_generation(self):
        """Verify Linux Bash one-liners are generated properly."""
        payload_net = LinuxPayloadGenerator.get_bash_payload("RED / CONEXION")
        self.assertIn("bash", payload_net)
        self.assertIn("curl", payload_net)
        self.assertIn("ip -4 addr", payload_net)

        payload_hw = LinuxPayloadGenerator.get_bash_payload("HARDWARE / CPU")
        self.assertIn("top -bn1", payload_hw)

    def test_windows_plugin_execution_dry_run(self):
        """Verify Windows plugin runs cleanly in dry run."""
        injector = DuckyInjector(dry_run=True)
        plugin = WindowsHIDPlugin(category="ANALISIS COMPLETO", keyboard_layout="es", injector=injector)
        result = plugin.run()
        self.assertEqual(result.status, DiagnosticStatus.SUCCESS)
        self.assertTrue(len(result.metrics) > 0)

    def test_linux_plugin_execution_dry_run(self):
        """Verify Linux plugin runs cleanly in dry run."""
        injector = DuckyInjector(dry_run=True)
        plugin = LinuxHIDPlugin(category="ANALISIS COMPLETO", keyboard_layout="es", injector=injector)
        result = plugin.run()
        self.assertEqual(result.status, DiagnosticStatus.SUCCESS)
        self.assertTrue(len(result.metrics) > 0)


if __name__ == "__main__":
    unittest.main()
