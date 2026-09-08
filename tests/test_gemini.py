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

    def test_local_fallback_osi_apipa_and_gateway_failure(self):
        """Verify fallback detects APIPA address and Gateway down."""
        data = {
            "os_type": "WINDOWS",
            "category": "RED / CONEXION",
            "telemetry": {"ip": "169.254.12.34", "ping_gateway": False},
            "osi_network": {
                "l3_network": {"ip": "169.254.12.34", "ping_gateway": False},
            },
        }
        res = self.analyzer.analyze_diagnostic(data)
        self.assertEqual(res["overall_status"], "CRIT")
        causes_str = " ".join(res["root_causes"])
        self.assertIn("APIPA", causes_str)
        self.assertIn("Gateway", causes_str)

    def test_local_fallback_osi_dns_and_captive_portal(self):
        """Verify fallback detects captive portal and DNS failure."""
        data = {
            "os_type": "LINUX",
            "category": "RED / CONEXION",
            "telemetry": {"ping_gateway": True, "ping_internet": True},
            "osi_network": {
                "l7_application": {"captive_portal": True, "dns_ok": False},
                "l3_network": {"ping_gateway": True, "ping_internet": True},
            },
        }
        res = self.analyzer.analyze_diagnostic(data)
        self.assertIn(res["overall_status"], ("WARN", "CRIT"))
        causes_str = " ".join(res["root_causes"])
        self.assertIn("cautivo", causes_str)
        self.assertIn("DNS", causes_str)

    def test_build_analysis_prompt_includes_osi_directives(self):
        """Verify the prompt explicitly instructs OSI Top-Down analysis."""
        prompt = self.analyzer._build_analysis_prompt({"os_type": "WINDOWS", "category": "RED"})
        self.assertIn("Modelo OSI", prompt)
        self.assertIn("Capa 7 a Capa 1", prompt)
        self.assertIn("Conecta los puntos", prompt)

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

    def test_default_model_is_gemini_3_5_flash(self):
        """Verify default model is set to gemini-3.5-flash."""
        self.assertEqual(self.analyzer.model, "gemini-3.5-flash")

    def test_custom_model_and_timeout(self):
        """Verify custom model and timeout are respected."""
        custom_analyzer = GeminiDiagnosticAnalyzer(api_key="test_key", model="gemini-2.5-flash", timeout_seconds=20)
        self.assertEqual(custom_analyzer.model, "gemini-2.5-flash")
        self.assertEqual(custom_analyzer.timeout_seconds, 20)
        self.assertEqual(custom_analyzer.api_key, "test_key")

    @patch("core.gemini_analyzer.requests.post")
    def test_gemini_api_400_invalid_key(self, mock_post):
        """Verify HTTP 400 returns specific API key invalid message."""
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = '{"error": {"message": "API key not valid"}}'
        mock_post.return_value = mock_resp

        analyzer = GeminiDiagnosticAnalyzer(api_key="invalid_key")
        res = analyzer.analyze_diagnostic({"os_type": "windows", "category": "RED", "telemetry": {}})
        self.assertIn("API Key de Gemini no válida (HTTP 400)", res["summary"])

    @patch("core.gemini_analyzer.requests.post")
    def test_gemini_api_403_forbidden(self, mock_post):
        """Verify HTTP 403 returns permission denied message."""
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = '{"error": {"message": "Permission denied"}}'
        mock_post.return_value = mock_resp

        analyzer = GeminiDiagnosticAnalyzer(api_key="test_key")
        res = analyzer.analyze_diagnostic({"os_type": "windows", "category": "RED", "telemetry": {}})
        self.assertIn("Acceso denegado / API Key no autorizada (HTTP 403)", res["summary"])

    @patch("core.gemini_analyzer.requests.post")
    def test_gemini_api_404_model_not_found(self, mock_post):
        """Verify HTTP 404 indicates model not found or deprecated."""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = '{"error": {"message": "Model not found"}}'
        mock_post.return_value = mock_resp

        analyzer = GeminiDiagnosticAnalyzer(api_key="test_key", model="gemini-1.5-flash")
        res = analyzer.analyze_diagnostic({"os_type": "windows", "category": "RED", "telemetry": {}})
        self.assertIn("Modelo Gemini no encontrado (gemini-1.5-flash) (HTTP 404)", res["summary"])

    @patch("core.gemini_analyzer.requests.post")
    def test_gemini_api_429_quota_exceeded(self, mock_post):
        """Verify HTTP 429 returns quota exceeded message."""
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = '{"error": {"message": "Resource exhausted"}}'
        mock_post.return_value = mock_resp

        analyzer = GeminiDiagnosticAnalyzer(api_key="test_key")
        res = analyzer.analyze_diagnostic({"os_type": "windows", "category": "RED", "telemetry": {}})
        self.assertIn("Cuota de API Gemini agotada", res["summary"])

    @patch("core.gemini_analyzer.requests.post")
    def test_gemini_api_connection_error(self, mock_post):
        """Verify ConnectionError indicates network or internet issue on Pi."""
        import requests
        mock_post.side_effect = requests.ConnectionError("Failed to resolve generativelanguage.googleapis.com")

        analyzer = GeminiDiagnosticAnalyzer(api_key="test_key")
        res = analyzer.analyze_diagnostic({"os_type": "windows", "category": "RED", "telemetry": {}})
        self.assertIn("Sin conexión a Internet en Raspberry Pi", res["summary"])

    @patch("core.gemini_analyzer.requests.post")
    def test_gemini_api_timeout(self, mock_post):
        """Verify Timeout indicates request timeout."""
        import requests
        mock_post.side_effect = requests.Timeout("Read timed out")

        analyzer = GeminiDiagnosticAnalyzer(api_key="test_key", timeout_seconds=10)
        res = analyzer.analyze_diagnostic({"os_type": "windows", "category": "RED", "telemetry": {}})
        self.assertIn("Timeout de conexión con Gemini (10s)", res["summary"])

    @patch("core.gemini_analyzer.requests.post")
    def test_gemini_api_success_200(self, mock_post):
        """Verify HTTP 200 parses JSON candidate response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": '{"summary": "Host en estado óptimo.", "overall_status": "OK", "root_causes": ["Sin fallas"], "action_plan": ["Continuar monitoreo"]}'
                            }
                        ]
                    }
                }
            ]
        }
        mock_post.return_value = mock_resp

        analyzer = GeminiDiagnosticAnalyzer(api_key="valid_key")
        res = analyzer.analyze_diagnostic({"os_type": "windows", "category": "RED", "telemetry": {}})
        self.assertEqual(res["overall_status"], "OK")
        self.assertEqual(res["summary"], "Host en estado óptimo.")
        self.assertEqual(res["root_causes"], ["Sin fallas"])
        self.assertEqual(res["action_plan"], ["Continuar monitoreo"])

    def test_local_fallback_hardware_smart_failure(self):
        """Verify fallback detects SMART impending failure as CRITICAL."""
        data = {
            "os_type": "WINDOWS",
            "category": "ANALISIS HARDWARE",
            "hardware_audit": {
                "storage": {
                    "physical_drives": [
                        {"model": "Seagate Barracuda 2TB", "smart_fail": True}
                    ]
                }
            }
        }
        res = self.analyzer.analyze_diagnostic(data)
        self.assertEqual(res["overall_status"], "CRIT")
        causes_str = " ".join(res["root_causes"])
        self.assertIn("SMART", causes_str)
        self.assertIn("Seagate", causes_str)

    def test_local_fallback_hardware_disk_full(self):
        """Verify fallback detects low disk space (< 5% as CRIT, < 10% as WARN)."""
        data_crit = {
            "os_type": "WINDOWS",
            "category": "ANALISIS HARDWARE",
            "hardware_audit": {
                "storage": {
                    "volumes": [
                        {"drive": "C:", "free_pct": 3.5}
                    ]
                }
            }
        }
        res_crit = self.analyzer.analyze_diagnostic(data_crit)
        self.assertEqual(res_crit["overall_status"], "CRIT")
        causes_crit = " ".join(res_crit["root_causes"])
        self.assertIn("< 5%", causes_crit)

        data_warn = {
            "os_type": "WINDOWS",
            "category": "ANALISIS HARDWARE",
            "hardware_audit": {
                "storage": {
                    "volumes": [
                        {"drive": "D:", "free_pct": 8.0}
                    ]
                }
            }
        }
        res_warn = self.analyzer.analyze_diagnostic(data_warn)
        self.assertEqual(res_warn["overall_status"], "WARN")
        causes_warn = " ".join(res_warn["root_causes"])
        self.assertIn("< 10%", causes_warn)

    def test_local_fallback_hardware_cpu_overheating(self):
        """Verify fallback detects severe CPU overheating (> 85°C as CRIT)."""
        data = {
            "os_type": "LINUX",
            "category": "ANALISIS HARDWARE",
            "hardware_audit": {
                "cpu": {"load_pct": 30.0, "temp_c": 91.5}
            }
        }
        res = self.analyzer.analyze_diagnostic(data)
        self.assertEqual(res["overall_status"], "CRIT")
        causes_str = " ".join(res["root_causes"])
        self.assertIn("91.5", causes_str)
        self.assertIn("Térmica", causes_str)

    def test_build_analysis_prompt_includes_malware_directives(self):
        """Verify the prompt explicitly instructs DFIR threat triage and malware checks."""
        prompt = self.analyzer._build_analysis_prompt({"os_type": "WINDOWS", "category": "MALWARE"})
        self.assertIn("DFIR Triage", prompt)
        self.assertIn("Posible Malware", prompt)
        self.assertIn("Command & Control", prompt)

    def test_local_fallback_malware_antivirus_disabled(self):
        """Verify fallback detects inactive or missing antivirus as CRITICAL."""
        data = {
            "os_type": "WINDOWS",
            "category": "ANALISIS MALWARE",
            "malware_audit": {
                "defenses": {"antivirus_name": "Ninguno", "antivirus_active": False, "firewall_enabled": False}
            }
        }
        res = self.analyzer.analyze_diagnostic(data)
        self.assertEqual(res["overall_status"], "CRIT")
        causes_str = " ".join(res["root_causes"])
        self.assertIn("antivirus", causes_str.lower())
        self.assertIn("firewall", causes_str.lower())

    def test_local_fallback_malware_suspicious_processes(self):
        """Verify fallback detects processes running from temp or masquerading as CRITICAL."""
        data = {
            "os_type": "LINUX",
            "category": "ANALISIS MALWARE",
            "malware_audit": {
                "defenses": {"antivirus_name": "ClamAV", "antivirus_active": True, "firewall_enabled": True},
                "suspicious_processes": [
                    {"pid": 9821, "name": "xmrig", "path": "/tmp/xmrig", "reason": "Ejecución desde /tmp"}
                ]
            }
        }
        res = self.analyzer.analyze_diagnostic(data)
        self.assertEqual(res["overall_status"], "CRIT")
        causes_str = " ".join(res["root_causes"])
        self.assertIn("xmrig", causes_str)
        self.assertIn("9821", causes_str)

    def test_local_fallback_malware_c2_connection_and_hosts_hijack(self):
        """Verify fallback flags suspicious C2 socket and hijacked hosts file as CRITICAL."""
        data = {
            "os_type": "WINDOWS",
            "category": "ANALISIS MALWARE",
            "malware_audit": {
                "defenses": {"antivirus_name": "Defender", "antivirus_active": True, "firewall_enabled": True},
                "network_c2": {
                    "established_connections": [
                        {"remote_ip": "185.220.101.5", "remote_port": 4444, "process": "powershell.exe", "suspicious": True}
                    ],
                    "hosts_file_hijack": True
                }
            }
        }
        res = self.analyzer.analyze_diagnostic(data)
        self.assertEqual(res["overall_status"], "CRIT")
        causes_str = " ".join(res["root_causes"])
        self.assertIn("185.220.101.5", causes_str)
        self.assertIn("C2", causes_str)
        self.assertIn("hosts", causes_str.lower())


if __name__ == "__main__":
    unittest.main()
