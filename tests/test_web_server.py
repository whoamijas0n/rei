"""
Tests for Local Telemetry Server (core/web_server.py)
"""

import time
import unittest
import requests

from core.web_server import REIWebServer


class TestWebServer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = REIWebServer(host="127.0.0.1", port=8899, base_url="http://127.0.0.1:8899")
        cls.server.start()
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def test_health_endpoint(self):
        """Verify /health returns HTTP 200 and json payload."""
        resp = requests.get("http://127.0.0.1:8899/health", timeout=3)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "ok")

    def test_report_lifecycle(self):
        """Verify posting diagnostic telemetry and retrieving mobile HTML and JSON reports."""
        payload = {
            "os_type": "windows",
            "category": "HARDWARE",
            "hostname": "TEST-PC",
            "telemetry": {
                "cpu_percent": 25.4,
                "ram_percent": 55.0,
            },
        }

        post_resp = requests.post("http://127.0.0.1:8899/api/v1/endpoint/report", json=payload, timeout=3)
        self.assertEqual(post_resp.status_code, 200)
        report_id = post_resp.json().get("report_id")
        self.assertIsNotNone(report_id)

        # Retrieve JSON report
        json_resp = requests.get(f"http://127.0.0.1:8899/api/v1/report/{report_id}", timeout=3)
        self.assertEqual(json_resp.status_code, 200)
        self.assertEqual(json_resp.json().get("hostname"), "TEST-PC")

        # Retrieve HTML report
        html_resp = requests.get(f"http://127.0.0.1:8899/report/{report_id}", timeout=3)
        self.assertEqual(html_resp.status_code, 200)
        self.assertIn("REI DIAGNOSTICS", html_resp.text)
        self.assertIn("TEST-PC", html_resp.text)

    def test_attach_ai_analysis(self):
        """Verify AI analysis attachment enriches stored report."""
        rep_id = self.server.store_local_report(
            os_type="LINUX",
            category="RED",
            hostname="linux-server",
            telemetry={"ip": "192.168.1.50"},
        )
        ai_data = {
            "summary": "Conexión nominal",
            "overall_status": "OK",
            "root_causes": ["Todo nominal"],
            "action_plan": ["Ninguna"],
        }
        self.server.attach_ai_analysis(rep_id, ai_data, overall_status="OK")

        report = self.server.get_latest_report()
        self.assertIsNotNone(report)
        self.assertEqual(report.ai_analysis, ai_data)


if __name__ == "__main__":
    unittest.main()
