"""
REI - Gemini Diagnostic Analyzer (core/gemini_analyzer.py)
Integrates Google Gemini LLM API (Free Tier) to provide executive summaries,
root cause analysis, and actionable remediation steps for endpoint and network telemetry.
"""

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger("REI.Core.GeminiAnalyzer")


class GeminiDiagnosticAnalyzer:
    """
    AI diagnostic analyzer powered by Google Gemini API with robust offline fallbacks.
    """

    DEFAULT_MODEL = "gemini-1.5-flash"
    DEFAULT_TIMEOUT_SECONDS = 10

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        config_path: str = "config/settings.json",
    ):
        self.config_path = config_path
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.api_key = api_key or self._load_api_key()

    def _load_api_key(self) -> Optional[str]:
        """Loads API key from environment variable or settings.json."""
        env_key = os.environ.get("GEMINI_API_KEY")
        if env_key and env_key.strip():
            return env_key.strip()

        if os.path.isfile(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    key = cfg.get("gemini", {}).get("api_key", "").strip()
                    if key:
                        return key
            except Exception as ex:
                logger.warning(f"Could not read API key from {self.config_path}: {ex}")

        return None

    def analyze_diagnostic(self, diagnostic_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submits telemetry payload to Gemini and parses structured response.
        Returns a dictionary with 'summary', 'root_causes', 'action_plan', and 'overall_status'.
        """
        if not self.api_key:
            logger.info("No Gemini API key found. Using local rule-based heuristic analysis.")
            return self._generate_local_fallback_analysis(diagnostic_data, reason="Sin API Key configurada.")

        prompt = self._build_analysis_prompt(diagnostic_data)

        # 1. Try google-genai official SDK if installed
        try:
            from google import genai
            client = genai.Client(api_key=self.api_key)
            response = client.models.generate_content(
                model=self.model,
                contents=prompt,
            )
            if response and response.text:
                parsed = self._parse_llm_json_response(response.text)
                if parsed:
                    return parsed
        except ImportError:
            logger.debug("google-genai SDK not installed, falling back to direct REST call.")
        except Exception as sdk_ex:
            logger.warning(f"google-genai SDK call failed ({sdk_ex}), attempting REST API fallback...")

        # 2. Direct HTTP REST API Call (Strict 10s timeout)
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
            headers = {"Content-Type": "application/json"}
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 600,
                    "responseMimeType": "application/json",
                },
            }

            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)

            if resp.status_code == 200:
                resp_json = resp.json()
                candidates = resp_json.get("candidates", [])
                if candidates:
                    raw_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    parsed = self._parse_llm_json_response(raw_text)
                    if parsed:
                        return parsed
            elif resp.status_code == 429:
                logger.warning("Gemini API quota exceeded (HTTP 429).")
                return self._generate_local_fallback_analysis(diagnostic_data, reason="Cuota de API Gemini agotada.")
            else:
                logger.error(f"Gemini API returned HTTP {resp.status_code}: {resp.text[:80]}")

        except requests.Timeout:
            logger.warning("Gemini API request timed out (10s limit).")
            return self._generate_local_fallback_analysis(diagnostic_data, reason="Timeout de conexión (10s).")
        except Exception as ex:
            logger.exception(f"Unexpected error calling Gemini API: {ex}")

        return self._generate_local_fallback_analysis(diagnostic_data, reason="Error de conectividad con Gemini.")

    def _build_analysis_prompt(self, data: Dict[str, Any]) -> str:
        """Constructs an expert IT diagnostician prompt."""
        data_str = json.dumps(data, indent=2, ensure_ascii=False)
        return f"""Eres un Ingeniero Principal de Soporte de TI y Ciberseguridad. Analiza la siguiente telemetría de un host diagnosticado por el dispositivo REI.

TELEMETRÍA:
{data_str}

Responde ÚNICAMENTE con un objeto JSON válido con la siguiente estructura exacta:
{{
  "summary": "Resumen ejecutivo de 2 líneas describiendo el estado general del equipo.",
  "overall_status": "OK" | "WARN" | "CRIT",
  "root_causes": [
    "Problema o anomalía 1 detectada",
    "Problema o anomalía 2 detectada"
  ],
  "action_plan": [
    "Paso 1 técnico o comando para resolver el problema",
    "Paso 2 técnico o comando recomendado"
  ]
}}"""

    def _parse_llm_json_response(self, text: str) -> Optional[Dict[str, Any]]:
        """Extracts and parses JSON object from LLM response text."""
        try:
            # Look for JSON object enclosed in {...}
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                clean_json_str = match.group(0).strip()
                data = json.loads(clean_json_str)
                if "summary" in data and "action_plan" in data:
                    return {
                        "summary": str(data.get("summary", "")).strip(),
                        "overall_status": str(data.get("overall_status", "OK")).upper(),
                        "root_causes": list(data.get("root_causes", [])),
                        "action_plan": list(data.get("action_plan", [])),
                    }
        except Exception as ex:
            logger.warning(f"Failed to parse LLM JSON: {ex} (Raw text: {text[:100]}...)")
        return None

    def _generate_local_fallback_analysis(self, data: Dict[str, Any], reason: str = "") -> Dict[str, Any]:
        """
        Rule-based heuristic fallback analysis when Gemini API is unreachable.
        Guarantees UI resiliency without throwing exceptions.
        """
        telemetry = data.get("telemetry", {})
        os_type = str(data.get("os_type", "")).upper()
        category = str(data.get("category", "")).upper()

        root_causes: List[str] = []
        action_plan: List[str] = []
        status = "OK"

        # Check CPU Temp / RAM
        cpu_usage = telemetry.get("cpu_percent") or telemetry.get("cpu_usage")
        if cpu_usage and isinstance(cpu_usage, (int, float)) and cpu_usage > 90:
            root_causes.append(f"Uso crítico de CPU al {cpu_usage}%")
            action_plan.append("Verificar procesos con alto consumo (Taskmgr / htop)")
            status = "WARN"

        mem_usage = telemetry.get("ram_percent") or telemetry.get("ram_usage")
        if mem_usage and isinstance(mem_usage, (int, float)) and mem_usage > 90:
            root_causes.append(f"Saturación de memoria RAM ({mem_usage}%)")
            action_plan.append("Reiniciar servicios o expandir memoria del host")
            status = "WARN"

        # Check Network
        ping_ok = telemetry.get("ping_gateway") or telemetry.get("ping_internet")
        if ping_ok is False:
            root_causes.append("Fallo de conectividad hacia la puerta de enlace o Internet")
            action_plan.append("Revisar cable ethernet / adaptador Wi-Fi y configuración DHCP/DNS")
            status = "CRIT"

        # Check Malware / Antivirus
        defender_active = telemetry.get("antivirus_enabled")
        if defender_active is False:
            root_causes.append("Protección antivirus desactivada en el host")
            action_plan.append("Habilitar Windows Defender o software antivirus corporativo")
            status = "CRIT"

        if not root_causes:
            root_causes.append("Parámetros nominales. No se encontraron fallas críticas.")
            action_plan.append("Mantener monitoreo preventivo y parches de seguridad al día.")

        summary_suffix = f" [{reason}]" if reason else ""
        summary = f"Diagnóstico {category} ({os_type}): Estado general {status}.{summary_suffix}"

        return {
            "summary": summary,
            "overall_status": status,
            "root_causes": root_causes,
            "action_plan": action_plan,
        }
