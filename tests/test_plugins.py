"""
Unit tests for Diagnostic Plugins and Manager.
"""

import os
import time
import unittest

os.environ["REI_DRY_RUN"] = "1"

from core.plugins import (
    WindowsDiagnosticPlugin,
    LinuxDiagnosticPlugin,
    SwitchDiagnosticPlugin,
    IPAddressPlugin,
    BatteryStatusPlugin,
    SystemStatusPlugin,
)
from core.manager import DiagnosticManager
from core.interfaces import DiagnosticResult, DiagnosticStatus, Severity


class TestPluginsAndManager(unittest.TestCase):
    """Verifies plugin contracts, execution safety, and background thread execution."""

    def test_windows_plugin_run(self):
        """Verifies WindowsDiagnosticPlugin executes safely and populates AI analysis."""
        plugin = WindowsDiagnosticPlugin()
        result = plugin.run()
        self.assertIsInstance(result, DiagnosticResult)
        self.assertEqual(result.plugin_name, "PC WINDOWS")
        self.assertIsNotNone(result.ai_analysis)
        self.assertIn(result.severity, [Severity.SALUDABLE, Severity.ADVERTENCIA, Severity.CRITICO])
        self.assertGreater(len(result.details), 0)

    def test_linux_plugin_run(self):
        """Verifies LinuxDiagnosticPlugin executes safely and populates AI analysis."""
        plugin = LinuxDiagnosticPlugin()
        result = plugin.run()
        self.assertIsInstance(result, DiagnosticResult)
        self.assertEqual(result.plugin_name, "PC LINUX")
        self.assertIsNotNone(result.ai_analysis)
        self.assertGreater(len(result.details), 0)

    def test_switch_plugin_run(self):
        """Verifies SwitchDiagnosticPlugin executes safely and populates AI analysis."""
        plugin = SwitchDiagnosticPlugin()
        result = plugin.run()
        self.assertIsInstance(result, DiagnosticResult)
        self.assertEqual(result.plugin_name, "SWITCH / RED")
        self.assertIsNotNone(result.ai_analysis)
        self.assertGreater(len(result.details), 0)

    def test_diagnostic_manager_async_execution(self):
        """Verifies asynchronous background thread execution and callbacks."""
        mgr = DiagnosticManager(max_workers=2)
        plugin = SystemStatusPlugin()
        mgr.register_plugin(plugin)

        completed_result = []

        def on_done(res: DiagnosticResult):
            completed_result.append(res)

        submitted = mgr.execute_async(
            plugin_id="diag_system",
            on_complete=on_done
        )
        self.assertTrue(submitted)

        # Wait for thread completion
        start = time.monotonic()
        while not completed_result and (time.monotonic() - start) < 3.0:
            time.sleep(0.05)

        self.assertEqual(len(completed_result), 1)
        self.assertEqual(completed_result[0].plugin_name, "ESTADO SISTEMA")
        mgr.shutdown(wait=False)


if __name__ == "__main__":
    unittest.main()
