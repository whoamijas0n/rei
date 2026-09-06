"""
Unit tests for AI Diagnostic Analyzer and Offline Heuristics Engine.
"""

import os
import unittest

os.environ["REI_DRY_RUN"] = "1"

from core.ai_client import DiagnosticAnalyzer
from core.interfaces import (
    EndpointDiagnosticData,
    NetworkSwitchDiagnosticData,
    StorageDriveInfo,
    Severity,
    AIAnalysisResult,
)


class TestDiagnosticAnalyzer(unittest.TestCase):
    """Verifies heuristic rules, severity assignment, 2-line OLED format, and report saving."""

    def setUp(self):
        self.analyzer = DiagnosticAnalyzer(api_key="")  # Empty key forces deterministic heuristics

    def test_healthy_endpoint_heuristic(self):
        """Verifies normal system metrics yield SALUDABLE status."""
        data = EndpointDiagnosticData(
            os_type="windows",
            os_version="Windows 11 Pro",
            hostname="WIN-OK",
            uptime_seconds=3600,
            cpu_model="Core i7",
            cpu_load_pct=15.0,
            ram_total_mb=16000,
            ram_used_mb=6000,  # 37.5%
            ram_free_mb=10000,
            drives=[
                StorageDriveInfo(device="C:", size_gb=500, free_gb=300, health_status="OK")
            ],
            critical_events=[]
        )
        analysis = self.analyzer.heuristic_analyze_endpoint(data)
        self.assertEqual(analysis.estado, Severity.SALUDABLE)
        self.assertLessEqual(len(analysis.diagnostico_corto.split("\n")), 2)
        self.assertGreater(len(analysis.acciones_recomendadas), 0)

    def test_saturated_ram_endpoint_heuristic(self):
        """Verifies RAM > 90% triggers CRÍTICO status."""
        data = EndpointDiagnosticData(
            os_type="linux",
            os_version="Ubuntu 22.04",
            hostname="LINUX-OOM",
            uptime_seconds=86400,
            cpu_model="Ryzen 7",
            cpu_load_pct=85.0,
            ram_total_mb=16000,
            ram_used_mb=15200,  # 95%
            ram_free_mb=800,
            drives=[
                StorageDriveInfo(device="/dev/nvme0n1", size_gb=500, free_gb=250, health_status="OK")
            ],
            critical_events=[]
        )
        analysis = self.analyzer.heuristic_analyze_endpoint(data)
        self.assertEqual(analysis.estado, Severity.CRITICO)
        self.assertIn("RAM", analysis.diagnostico_corto)

    def test_smart_predictive_failure_heuristic(self):
        """Verifies SMART failure prediction triggers CRÍTICO and backup recommendations."""
        data = EndpointDiagnosticData(
            os_type="windows",
            os_version="Windows 11",
            hostname="WIN-SMART",
            uptime_seconds=5000,
            cpu_model="Intel Core i5",
            cpu_load_pct=10.0,
            ram_total_mb=8000,
            ram_used_mb=3000,
            ram_free_mb=5000,
            drives=[
                StorageDriveInfo(
                    device="C:",
                    size_gb=256,
                    free_gb=50,
                    health_status="PREDICT_FAILURE",
                    smart_alerts=["SMART Failure Predicted"]
                )
            ],
            critical_events=[]
        )
        analysis = self.analyzer.heuristic_analyze_endpoint(data)
        self.assertEqual(analysis.estado, Severity.CRITICO)
        self.assertIn("SMART", analysis.diagnostico_corto)
        self.assertTrue(any("Respaldar" in a or "disco" in a for a in analysis.acciones_recomendadas))

    def test_bsod_critical_event_heuristic(self):
        """Verifies Windows Event Log BSODs trigger CRÍTICO."""
        data = EndpointDiagnosticData(
            os_type="windows",
            os_version="Windows 11",
            hostname="WIN-BSOD",
            uptime_seconds=1200,
            cpu_model="Intel Core i9",
            cpu_load_pct=20.0,
            ram_total_mb=32000,
            ram_used_mb=8000,
            ram_free_mb=24000,
            drives=[
                StorageDriveInfo(device="C:", size_gb=1000, free_gb=700, health_status="OK")
            ],
            critical_events=["2026-09-04 10:00 [ID 41] Kernel-Power: The system has rebooted unexpectedly"]
        )
        analysis = self.analyzer.heuristic_analyze_endpoint(data)
        self.assertEqual(analysis.estado, Severity.CRITICO)
        self.assertIn("Reinicio abrupto", analysis.diagnostico_corto)

    def test_switch_psu_fault_heuristic(self):
        """Verifies PSU fault on network switch triggers CRÍTICO status."""
        data = NetworkSwitchDiagnosticData(
            hostname="SW-EDGE-01",
            model="WS-C3750X-48P",
            ios_version="15.2(4)E7",
            uptime="50 weeks",
            total_ports=52,
            ports_up=40,
            ports_down=12,
            ports_err_disabled=[],
            ports_with_crc_errors={},
            power_supplies={"PS1": "OK", "PS2": "FAULT"},
            critical_syslog=[]
        )
        analysis = self.analyzer.heuristic_analyze_switch(data)
        self.assertEqual(analysis.estado, Severity.CRITICO)
        self.assertIn("PS2", analysis.diagnostico_corto)
        self.assertTrue(any("PS2" in a or "fuente" in a for a in analysis.acciones_recomendadas))

    def test_switch_err_disabled_ports_heuristic(self):
        """Verifies err-disabled ports trigger ADVERTENCIA status."""
        data = NetworkSwitchDiagnosticData(
            hostname="SW-ACCESS-02",
            model="C9200L-24T-4G",
            ios_version="17.3.3",
            uptime="10 weeks",
            total_ports=28,
            ports_up=20,
            ports_down=8,
            ports_err_disabled=["Gi1/0/12"],
            ports_with_crc_errors={"Gi1/0/5": 14},
            power_supplies={"PS1": "OK"},
            critical_syslog=[]
        )
        analysis = self.analyzer.heuristic_analyze_switch(data)
        self.assertEqual(analysis.estado, Severity.ADVERTENCIA)
        self.assertIn("Err-disable", analysis.diagnostico_corto)

    def test_report_persistence(self):
        """Verifies audit reports are written to disk under data/reports/."""
        data = EndpointDiagnosticData(
            os_type="linux",
            os_version="Debian 12",
            hostname="test-persist",
            uptime_seconds=100,
            cpu_model="ARM Cortex-A53",
            cpu_load_pct=5.0,
            ram_total_mb=512,
            ram_used_mb=200,
            ram_free_mb=312,
            drives=[StorageDriveInfo(device="/dev/mmcblk0p2", size_gb=16, free_gb=8, health_status="OK")],
            critical_events=[]
        )
        analysis = self.analyzer.analyze_endpoint(data)
        self.assertIsInstance(analysis, AIAnalysisResult)

        # Check that reports directory has generated file
        files = [f for f in os.listdir(self.analyzer.REPORTS_DIR) if "test-persist" in f or "test_persist" in f]
        self.assertGreater(len(files), 0, "Report file was not persisted to disk.")



if __name__ == "__main__":
    unittest.main()
