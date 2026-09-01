"""
Tests for Gemini Diagnostic Analyzer (core/gemini_analyzer.py)
"""

import unittest
from unittest.mock import MagicMock, patch

from core.gemini_analyzer import GeminiDiagnosticAnalyzer


class TestGeminiAnalyzer(unittest.TestCase):

    def setUp(self):
        self.analyzer = GeminiDiagnosticAnalyzer(api_key=None)

    def test_local_fallback_analysis_ok(self):
        """Verify fallback returns valid structure for nominal data."""
        data = {
            "os_type": "WINDOWS",
            "category": "RED / CONEXION",
            "telemetry": {
                "cpu_percent": 15.0,
                "ram_percent": 40.0,
                "ping_gateway": True,
                "antivirus_enabled": "Defender",
            },
        }
        res = self.analyzer.analyze_diagnostic(data)
        self.assertIn("summary", res)
        self.assertIn("overall_status", res)
        self.assertIn("root_causes", res)
        self.assertIn("action_plan", res)
        self.assertEqual(res["overall_status"], "OK")

    def test_local_fallback_analysis_warning(self):
        """Verify fallback detects high CPU/RAM thresholds."""
        data = {
            "os_type": "LINUX",
            "category": "HARDWARE",
            "telemetry": {
                "cpu_percent": 95.0,
                "ram_percent": 92.0,
            },
        }
        res = self.analyzer.analyze_diagnostic(data)
        self.assertEqual(res["overall_status"], "WARN")
        self.assertTrue(len(res["root_causes"]) >= 2)

    def test_json_parsing_with_code_fences(self):
        """Verify clean extraction of JSON enclosed in markdown code fences."""
        raw_llm = """```json
        {
            "summary": "Anomalías en adaptador de red",
            "overall_status": "WARN",
            "root_causes": ["Fallo en Gateway"],
            "action_plan": ["Reiniciar adaptador"]
        }
        ```"""
        parsed = self.analyzer._parse_llm_json_response(raw_llm)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["summary"], "Anomalías en adaptador de red")
        self.assertEqual(parsed["overall_status"], "WARN")
        self.assertEqual(parsed["root_causes"], ["Fallo en Gateway"])


if __name__ == "__main__":
    unittest.main()
