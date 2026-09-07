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

    DEFAULT_MODEL = "gemini-3.5-flash"
    DEFAULT_TIMEOUT_SECONDS = 15

    _DEFAULT_CONFIG_SENTINEL = object()

    def __init__(
        self,
        api_key: Any = _DEFAULT_CONFIG_SENTINEL,
        model: Any = _DEFAULT_CONFIG_SENTINEL,
        timeout_seconds: Any = _DEFAULT_CONFIG_SENTINEL,
        config_path: str = "config/settings.json",
    ):
        self.config_path = self._resolve_config_path(config_path)
        loaded_cfg = self._load_configuration()

        if api_key is self._DEFAULT_CONFIG_SENTINEL:
            self.api_key = os.environ.get("GEMINI_API_KEY") or loaded_cfg.get("api_key")
        else:
            self.api_key = api_key

        if model is self._DEFAULT_CONFIG_SENTINEL:
            self.model = os.environ.get("GEMINI_MODEL") or loaded_cfg.get("model") or self.DEFAULT_MODEL
        else:
            self.model = model or self.DEFAULT_MODEL

        if timeout_seconds is self._DEFAULT_CONFIG_SENTINEL:
            self.timeout_seconds = loaded_cfg.get("timeout_seconds") or self.DEFAULT_TIMEOUT_SECONDS
        else:
            self.timeout_seconds = timeout_seconds or self.DEFAULT_TIMEOUT_SECONDS

    def _resolve_config_path(self, path: str) -> str:
        """Resolves configuration path considering relative and project root locations."""
        if os.path.isabs(path) and os.path.isfile(path):
            return path

        candidates = [
            path,
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", path),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "settings.json"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "settings.json"),
            os.path.join(os.getcwd(), "config", "settings.json"),
            os.path.join(os.getcwd(), "settings.json"),
        ]
        for candidate in candidates:
            if os.path.isfile(candidate):
                return os.path.abspath(candidate)
        return path

    def _load_configuration(self) -> Dict[str, Any]:
        """Loads configuration from .env, settings.local.json, or settings.json."""
        config_data: Dict[str, Any] = {}

        # 1. Check .env in project root or current directory
        base_dir = os.path.dirname(os.path.abspath(self.config_path))
        possible_envs = [
            os.path.join(base_dir, "..", ".env"),
            os.path.join(base_dir, ".env"),
            os.path.join(os.getcwd(), ".env"),
        ]
        for dotenv_path in possible_envs:
            if os.path.isfile(dotenv_path):
                try:
                    with open(dotenv_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith("GEMINI_API_KEY="):
                                val = line.split("=", 1)[1].strip().strip('"\'')
                                if val:
                                    config_data["api_key"] = val
                            elif line.startswith("GEMINI_MODEL="):
                                val = line.split("=", 1)[1].strip().strip('"\'')
                                if val:
                                    config_data["model"] = val
                except Exception as ex:
                    logger.debug(f"Could not read .env from {dotenv_path}: {ex}")

        # 2. Check local settings override (settings.local.json)
        local_cfg = os.path.join(os.path.dirname(self.config_path), "settings.local.json")
        if os.path.isfile(local_cfg):
            try:
                with open(local_cfg, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    gem_cfg = cfg.get("gemini", {})
                    if gem_cfg.get("api_key"):
                        config_data["api_key"] = gem_cfg.get("api_key", "").strip()
                    if gem_cfg.get("model"):
                        config_data["model"] = gem_cfg.get("model", "").strip()
                    if gem_cfg.get("timeout_seconds"):
                        config_data["timeout_seconds"] = int(gem_cfg.get("timeout_seconds"))
            except Exception as ex:
                logger.debug(f"Could not read settings from {local_cfg}: {ex}")

        # 3. Check primary settings.json
        if os.path.isfile(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    gem_cfg = cfg.get("gemini", {})
                    if "api_key" not in config_data and gem_cfg.get("api_key"):
                        config_data["api_key"] = gem_cfg.get("api_key", "").strip()
                    if "model" not in config_data and gem_cfg.get("model"):
                        config_data["model"] = gem_cfg.get("model", "").strip()
                    if "timeout_seconds" not in config_data and gem_cfg.get("timeout_seconds"):
                        config_data["timeout_seconds"] = int(gem_cfg.get("timeout_seconds"))
            except Exception as ex:
                logger.warning(f"Could not read settings from {self.config_path}: {ex}")

        return config_data

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

        # 2. Direct HTTP REST API Call
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
            headers = {"Content-Type": "application/json"}
            
            gen_config: Dict[str, Any] = {
                "temperature": 0.2,
                "maxOutputTokens": 2048,
                "responseMimeType": "application/json",
            }
            # For thinking models (Gemini 2.5/3.x), disable internal reasoning tokens to prevent token exhaustion and timeouts
            if any(token in self.model.lower() for token in ["3.", "2.5", "thinking"]):
                gen_config["thinkingConfig"] = {"thinkingBudget": 0}

            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": gen_config,
            }

            resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)

            # If the model does not support thinkingConfig and returns 400, retry once without it
            if resp.status_code == 400 and "thinkingConfig" in gen_config:
                logger.debug("Model rejected thinkingConfig, retrying standard payload...")
                gen_config.pop("thinkingConfig", None)
                payload["generationConfig"] = gen_config
                resp = requests.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)

            if resp.status_code == 200:
                resp_json = resp.json()
                candidates = resp_json.get("candidates", [])
                if candidates:
                    finish_reason = candidates[0].get("finishReason", "")
                    raw_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    parsed = self._parse_llm_json_response(raw_text)
                    if parsed:
                        return parsed
                    if finish_reason == "MAX_TOKENS":
                        return self._generate_local_fallback_analysis(diagnostic_data, reason="Respuesta de Gemini truncada por límite de tokens.")
                return self._generate_local_fallback_analysis(diagnostic_data, reason="Respuesta de Gemini vacía o no estructurada.")
            elif resp.status_code == 400:
                logger.error(f"Gemini API returned HTTP 400: {resp.text[:120]}")
                return self._generate_local_fallback_analysis(diagnostic_data, reason="API Key de Gemini no válida (HTTP 400).")
            elif resp.status_code == 403:
                logger.error(f"Gemini API returned HTTP 403: {resp.text[:120]}")
                return self._generate_local_fallback_analysis(diagnostic_data, reason="Acceso denegado / API Key no autorizada (HTTP 403).")
            elif resp.status_code == 404:
                logger.error(f"Gemini API returned HTTP 404: {resp.text[:120]}")
                return self._generate_local_fallback_analysis(diagnostic_data, reason=f"Modelo Gemini no encontrado ({self.model}) (HTTP 404).")
            elif resp.status_code == 429:
                logger.warning("Gemini API quota exceeded (HTTP 429).")
                return self._generate_local_fallback_analysis(diagnostic_data, reason="Cuota de API Gemini agotada (HTTP 429).")
            elif resp.status_code >= 500:
                logger.error(f"Gemini API server error HTTP {resp.status_code}: {resp.text[:120]}")
                return self._generate_local_fallback_analysis(diagnostic_data, reason=f"Fallo en servidores de Google Gemini (HTTP {resp.status_code}).")
            else:
                logger.error(f"Gemini API returned HTTP {resp.status_code}: {resp.text[:120]}")
                return self._generate_local_fallback_analysis(diagnostic_data, reason=f"Error HTTP {resp.status_code} en Gemini API.")

        except requests.Timeout:
            logger.warning(f"Gemini API request timed out ({self.timeout_seconds}s limit).")
            return self._generate_local_fallback_analysis(diagnostic_data, reason=f"Timeout de conexión con Gemini ({self.timeout_seconds}s).")
        except requests.ConnectionError as conn_ex:
            logger.warning(f"Connection error calling Gemini API: {conn_ex}")
            return self._generate_local_fallback_analysis(diagnostic_data, reason="Sin conexión a Internet en Raspberry Pi / Error de red.")
        except Exception as ex:
            logger.exception(f"Unexpected error calling Gemini API: {ex}")
            return self._generate_local_fallback_analysis(diagnostic_data, reason=f"Error inesperado al conectar con Gemini: {type(ex).__name__}.")

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
