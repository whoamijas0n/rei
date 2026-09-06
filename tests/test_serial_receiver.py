"""
Unit tests for USB CDC-ACM Serial Receiver.
"""

import json
import os
import unittest

os.environ["REI_DRY_RUN"] = "1"

from core.serial_receiver import (
    USBSerialReceiver,
    get_windows_collector_script,
    get_linux_collector_script,
    BEGIN_MARKER,
    END_MARKER,
)
from core.interfaces import EndpointDiagnosticData


class TestUSBSerialReceiver(unittest.TestCase):
    """Verifies CDC-ACM listener, frame parsing, schema validation, and simulation."""

    def setUp(self):
        self.receiver = USBSerialReceiver(port="/dev/null_mock")

    def test_simulation_mode(self):
        """Verifies receiver is in simulation mode when hardware port is absent."""
        self.assertTrue(self.receiver.is_simulated)

    def test_script_generators(self):
        """Verifies collector scripts contain required CIM/WMI and bash markers."""
        win_script = get_windows_collector_script()
        self.assertIn("Win32_OperatingSystem", win_script)
        self.assertIn("MSStorageDriver_FailurePredictStatus", win_script)
        self.assertIn(BEGIN_MARKER, win_script)
        self.assertIn(END_MARKER, win_script)

        linux_script = get_linux_collector_script()
        self.assertIn("free -m", linux_script)
        self.assertIn("systemctl --failed", linux_script)
        self.assertIn(BEGIN_MARKER, linux_script)
        self.assertIn(END_MARKER, linux_script)

    def test_parse_valid_json_payload(self):
        """Verifies parsing of framed JSON data into EndpointDiagnosticData."""
        raw_json = json.dumps({
            "os_type": "windows",
            "os_version": "Windows 10 Pro",
            "hostname": "TEST-PC",
            "uptime_seconds": 12000,
            "cpu_model": "Core i5-10400",
            "cpu_load_pct": 22.0,
            "ram_total_mb": 16000,
            "ram_used_mb": 8000,
            "ram_free_mb": 8000,
            "drives": [
                {"device": "C:", "size_gb": 500, "free_gb": 200, "health_status": "OK", "smart_alerts": []}
            ],
            "critical_events": [],
            "network_adapters": [{"name": "Ethernet", "ip": "192.168.1.50"}]
        })

        parsed = self.receiver._parse_json_payload(raw_json)
        self.assertIsNotNone(parsed)
        self.assertIsInstance(parsed, EndpointDiagnosticData)
        self.assertEqual(parsed.hostname, "TEST-PC")
        self.assertEqual(parsed.os_type, "windows")
        self.assertEqual(len(parsed.drives), 1)
        self.assertEqual(parsed.drives[0].device, "C:")

    def test_parse_corrupt_json_payload(self):
        """Verifies graceful handling of invalid/truncated JSON."""
        corrupt = '{"os_type": "windows", "hostname":'
        parsed = self.receiver._parse_json_payload(corrupt)
        self.assertIsNone(parsed)

    def test_simulated_wait_windows(self):
        """Verifies simulated data delivery for Windows endpoint."""
        data = self.receiver.wait_for_endpoint_data(timeout=2.0, expected_os="windows")
        self.assertIsNotNone(data)
        self.assertEqual(data.os_type, "windows")
        self.assertIn("Windows", data.os_version)
        self.assertGreater(data.ram_total_mb, 0)
        self.assertGreater(len(data.drives), 0)

    def test_simulated_wait_linux(self):
        """Verifies simulated data delivery for Linux endpoint."""
        data = self.receiver.wait_for_endpoint_data(timeout=2.0, expected_os="linux")
        self.assertIsNotNone(data)
        self.assertEqual(data.os_type, "linux")
        self.assertIn("Ubuntu", data.os_version)
        self.assertGreater(data.ram_total_mb, 0)


if __name__ == "__main__":
    unittest.main()
