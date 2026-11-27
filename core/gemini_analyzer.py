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

    @staticmethod
    def _extract_switch_audit(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Devuelve el bloque switch_audit si el payload corresponde a un switch."""
        if not isinstance(data, dict):
            return None
        direct = data.get("switch_audit")
        if isinstance(direct, dict) and direct:
            return direct
        telemetry = data.get("telemetry")
        if isinstance(telemetry, dict):
            nested = telemetry.get("switch_audit")
            if isinstance(nested, dict) and nested:
                return nested
        if str(data.get("device_kind", "")).upper() == "SWITCH":
            return {}
        if str(data.get("os_type", "")).upper().startswith("SWITCH_"):
            return {}
        return None

    def _build_switch_analysis_prompt(self, data: Dict[str, Any], switch_audit: Dict[str, Any]) -> str:
        """Construye un prompt especializado en switches de red (L1/L2/L3, PoE, STP, salud)."""
        # Excluir raw_commands extensos para no saturar el contexto del LLM
        compact_audit = {
            k: v for k, v in switch_audit.items() if k != "raw_commands"
        }
        category_label = str(
            compact_audit.get("category_label")
            or data.get("category")
            or "PROBLEMAS GENERALES"
        )
        vendor_name = str(compact_audit.get("vendor_name") or data.get("os_type") or "SWITCH")
        data_str = json.dumps(compact_audit, indent=2, ensure_ascii=False)

        return f"""Eres un Ingeniero Principal de Redes y Switching Empresarial (CCIE / infraestructura L1-L3). Analiza la siguiente telemetría recolectada por consola serial por el dispositivo de campo REI para el switch ({vendor_name}), enfocándote en la categoría de diagnóstico: {category_label}.

TELEMETRÍA DEL SWITCH:
{data_str}

DIRECTIVAS CRÍTICAS DE ANÁLISIS:
1. Salud Física y Chasis:
   - Evalúa ventiladores, temperatura, fuentes de poder (PSU), carga de CPU y uso de memoria. Cualquier falla de ventilador/PSU o temperatura crítica es CRIT.
2. Puertos, Capa 1/2 y Errores:
   - Detecta puertos en estado err-disabled e indica el motivo exacto (bpduguard, psecure-violation, link-flap, etc.).
   - Analiza errores CRC/FCS/Align/Rcv en interfaces activas y enlaces en half-duplex (posible cable defectuoso, transceptor SFP o duplex mismatch).
   - Evalúa estado de uplinks (especialmente SFP+ 10G) y vecinos CDP/LLDP.
3. Energía PoE:
   - Si el switch soporta PoE, evalúa el consumo frente al presupuesto total (>85% es WARN) y reporta de inmediato cualquier puerto PoE en falla (faulty / power-deny) como CRIT.
4. Conmutación y Estabilidad:
   - Revisa modo Spanning Tree, VLANs activas y eventos críticos en el buffer de logging.
5. Plan de Acción:
   - Proporciona comandos CLI exactos de verificación y remediación acordes al fabricante detectado ({vendor_name}).

Responde ÚNICAMENTE con un objeto JSON válido con la siguiente estructura exacta:
{{
  "summary": "Resumen ejecutivo de 2 a 3 líneas del estado del switch, fallas críticas y severidad.",
  "overall_status": "OK" | "WARN" | "CRIT",
  "root_causes": [
    "Causa raíz 1 identificada (indicando subsistema: Chasis, PoE, Capa 1/2, STP o Seguridad)",
    "Causa raíz 2 identificada"
  ],
  "action_plan": [
    "Comando CLI o acción técnica 1 de verificación/remediación",
    "Comando CLI o acción técnica 2 recomendada"
  ]
}}"""

    def _build_analysis_prompt(self, data: Dict[str, Any]) -> str:
        """Constructs an expert IT, Network, and Hardware diagnostician prompt following the OSI Top-Down model and hardware subsystem analysis."""
        switch_audit = self._extract_switch_audit(data)
        if switch_audit is not None:
            return self._build_switch_analysis_prompt(data, switch_audit)

        data_str = json.dumps(data, indent=2, ensure_ascii=False)
        return f"""Eres un Ingeniero Principal de Redes, Infraestructura TI, Diagnóstico de Hardware y Ciberseguridad. Analiza la siguiente telemetría de un host (identidad de hardware, subsistemas físicos de Placa/CPU/RAM/Almacenamiento/GPU, y pila de red organizada según el Modelo OSI: Capa 7 a Capa 1) recolectada por el dispositivo de campo REI.

TELEMETRÍA RECOLECTADA:
{data_str}

DIRECTIVAS CRÍTICAS DE ANÁLISIS:
1. Conecta los puntos en Red (Modelo OSI Top-Down):
   - Si la Capa 7 falla (resolución DNS o tráfico HTTP), determina si la causa raíz es de Capa 3 (pérdida de paquetes, ruta por defecto, ping Gateway/Internet), Capa 2 (falla de DHCP, dirección APIPA 169.254, baja señal Wi-Fi < 40%, conflicto ARP) o Capa 1 (cable desconectado, carrier down, negociación a 100M).
   - Identifica con precisión el dominio de la falla: ¿Es problema del ENDPOINT (driver, adaptador, IP estática errónea, portal cautivo no autenticado) o de la INFRAESTRUCTURA (DHCP agotado, switch caído, DNS caído, corte del ISP)?
2. Audita los Subsistemas de Hardware:
   - Almacenamiento y Salud SMART: Si alguna unidad física predice fallos SMART (PredictFailure / smart_fail), catalógala inmediatamente como CRÍTICO y prioriza respaldo.
   - Espacio en Disco: Si alguna partición tiene < 10% de espacio disponible, alertar riesgo de bloqueo; si tiene < 5%, marcar CRÍTICO.
   - Procesador y Térmica: Correlaciona carga de CPU y temperatura. Si supera los 80°C, alerta sobre estrangulamiento térmico (thermal throttling).
   - Memoria RAM: Evalúa saturación (> 90%) y slots/módulos para posibles cuellos de botella o necesidad de ampliación.
   - Energía / Batería: Evalúa nivel de carga y desgaste en equipos portátiles.
3. Audita la Seguridad, Amenazas y Posible Malware (DFIR Triage):
   - Defensas del Endpoint: Si el antivirus está inactivo o ausente, o el cortafuegos deshabilitado, cataloga como riesgo CRÍTICO.
   - Procesos Sospechosos y Memoria: Evalúa procesos ejecutándose desde directorios temporales (%TEMP%, AppData, /tmp, /dev/shm), suplantación de nombres (masquerading como svchost/lsass fuera de System32), o binarios eliminados en memoria (deleted).
   - Persistencia: Evalúa claves Run/RunOnce, Carpeta de Inicio (Startup), Crontabs de usuario y servicios systemd locales.
   - Conexiones y Sockets C2: Detecta conexiones salientes hacia puertos comunes de Command & Control o reverse shells (4444, 1337, 6667, 8888, 9001, 31337). Si existen, marcar inmediatamente como CRÍTICO.
   - Integridad del Sistema: Si el archivo hosts presenta redirecciones de dominios de seguridad o actualización, marcar de inmediato como CRÍTICO.
   - Artefactos Recientes: Alerta sobre binarios o scripts sospechosos depositados en carpetas temporales durante los últimos 7 días.
4. Proporciona comandos técnicos de remediación accionables y específicos para el sistema operativo detectado (PowerShell / WMI para Windows, bash / ip / smartctl / systemctl para Linux).

Responde ÚNICAMENTE con un objeto JSON válido con la siguiente estructura exacta:
{{
  "summary": "Resumen ejecutivo de 2 a 3 líneas del estado del equipo, hardware, seguridad/amenazas y red, indicando causas principales y severidad.",
  "overall_status": "OK" | "WARN" | "CRIT",
  "root_causes": [
    "Causa raíz 1 identificada (indicando subsistema de hardware, seguridad o capa OSI afectada)",
    "Causa raíz 2 identificada"
  ],
  "action_plan": [
    "Comando de terminal o acción técnica 1 para solucionar la falla",
    "Comando de terminal o acción técnica 2 recomendado"
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

    def _generate_switch_local_fallback_analysis(
        self,
        data: Dict[str, Any],
        switch_audit: Dict[str, Any],
        reason: str = "",
    ) -> Dict[str, Any]:
        """Análisis heurístico local para switches cuando Gemini no está disponible."""
        identity = switch_audit.get("identity", {}) if isinstance(switch_audit.get("identity"), dict) else {}
        health = switch_audit.get("health", {}) if isinstance(switch_audit.get("health"), dict) else {}
        env = health.get("environment", {}) if isinstance(health.get("environment"), dict) else {}
        cpu = health.get("cpu", {}) if isinstance(health.get("cpu"), dict) else {}
        mem = health.get("memory", {}) if isinstance(health.get("memory"), dict) else {}
        poe = switch_audit.get("poe", {}) if isinstance(switch_audit.get("poe"), dict) else {}
        ifaces = switch_audit.get("interfaces", {}) if isinstance(switch_audit.get("interfaces"), dict) else {}
        logs = switch_audit.get("logging", {}) if isinstance(switch_audit.get("logging"), dict) else {}

        hostname = str(identity.get("hostname") or data.get("hostname") or "Switch")
        vendor = str(switch_audit.get("vendor_name") or data.get("os_type") or "SWITCH")
        category = str(switch_audit.get("category_label") or data.get("category") or "PROBLEMAS GENERALES")

        root_causes: List[str] = []
        action_plan: List[str] = []
        status = "OK"

        # 1. Salud ambiental (ventiladores, temperatura, PSU)
        for issue in env.get("issues", []) if isinstance(env.get("issues"), list) else []:
            issue_str = str(issue)
            root_causes.append(f"[Chasis] {issue_str}")
            action_plan.append("Verificar ventilacion del rack, fuentes de poder y ejecutar: show env all")
            if any(k in issue_str.upper() for k in ("FAIL", "FAULT", "CRIT", "RED")):
                status = "CRIT"
            elif status != "CRIT":
                status = "WARN"

        # 2. Puertos err-disabled
        err_dis = ifaces.get("err_disabled", []) if isinstance(ifaces.get("err_disabled"), list) else []
        for entry in err_dis[:5]:
            if isinstance(entry, dict):
                p = entry.get("port", "puerto")
                rsn = entry.get("reason", "err-disabled")
                root_causes.append(f"[Interfaces] Puerto {p} bloqueado en err-disabled (motivo: {rsn})")
                action_plan.append(
                    f"Corregir causa ({rsn}) en {p} y reactivar: interface {p} -> shutdown / no shutdown"
                )
                status = "CRIT"

        # 3. PoE (fallas o saturación de presupuesto)
        faulty_poe = poe.get("faulty_ports", []) if isinstance(poe.get("faulty_ports"), list) else []
        for fp in faulty_poe[:4]:
            if isinstance(fp, dict):
                p = fp.get("port", "puerto")
                op = fp.get("oper", "faulty")
                root_causes.append(f"[PoE] Falla de alimentacion PoE en {p} (estado: {op})")
                action_plan.append(f"Revisar cableado/equipo conectado en {p} y verificar: show power inline {p} detail")
                status = "CRIT"

        poe_pct = poe.get("usage_pct")
        if isinstance(poe_pct, (int, float)) and poe_pct >= 85.0:
            root_causes.append(f"[PoE] Consumo cercano al limite del presupuesto ({poe_pct}%)")
            action_plan.append("Revisar consumo por puerto con 'show power inline' y redistribuir cargas PoE")
            if status != "CRIT":
                status = "WARN"

        # 4. CPU y Memoria
        cpu_5m = cpu.get("cpu_5m")
        if isinstance(cpu_5m, (int, float)) and cpu_5m >= 85:
            root_causes.append(f"[CPU] Carga critica de CPU ({cpu_5m}% en 5 min)")
            action_plan.append("Identificar procesos activos con: show processes cpu sorted")
            status = "CRIT"
        elif isinstance(cpu_5m, (int, float)) and cpu_5m >= 70:
            root_causes.append(f"[CPU] Carga elevada de CPU ({cpu_5m}% en 5 min)")
            action_plan.append("Monitorear carga de control plane con: show processes cpu history")
            if status != "CRIT":
                status = "WARN"

        mem_pct = mem.get("usage_pct")
        if isinstance(mem_pct, (int, float)) and mem_pct >= 90.0:
            root_causes.append(f"[Memoria] Uso elevado de memoria del switch ({mem_pct}%)")
            action_plan.append("Revisar asignacion de memoria con: show processes memory sorted")
            if status != "CRIT":
                status = "WARN"

        # 5. Errores CRC/FCS y Half-Duplex
        err_counters = ifaces.get("error_counters", []) if isinstance(ifaces.get("error_counters"), list) else []
        for ec in err_counters[:3]:
            if isinstance(ec, dict) and int(ec.get("total", 0)) >= 10:
                p = ec.get("port", "puerto")
                tot = ec.get("total", 0)
                fcs = ec.get("fcs", 0)
                root_causes.append(f"[Capa 1/2] Puerto {p} acumula {tot} errores de trama (FCS/CRC: {fcs})")
                action_plan.append(f"Revisar patch cord/transceptor en {p} y validar: show interface {p}")
                if status != "CRIT":
                    status = "WARN"

        hd_ports = ifaces.get("half_duplex_ports", []) if isinstance(ifaces.get("half_duplex_ports"), list) else []
        if hd_ports:
            p_list = ", ".join(str(p.get("port", "")) for p in hd_ports[:4] if isinstance(p, dict))
            root_causes.append(f"[Duplex] Puertos negociados en half-duplex: {p_list}")
            action_plan.append("Alinear autonegociacion speed/duplex en ambos extremos del enlace")
            if status != "CRIT":
                status = "WARN"

        # 6. Eventos críticos en syslog si aún no hay hallazgos
        crit_logs = logs.get("critical_events", []) if isinstance(logs.get("critical_events"), list) else []
        if crit_logs and not root_causes:
            root_causes.append(f"[Logs] Se registraron {len(crit_logs)} evento(s) relevante(s) en syslog reciente")
            action_plan.append("Inspeccionar eventos recientes en detalle con: show logging")
            if status != "CRIT":
                status = "WARN"

        if not root_causes:
            root_causes.append(
                f"Switch {hostname} en estado nominal. Sin puertos err-disabled, fallas PoE ni alertas de hardware."
            )
            action_plan.append("Mantener respaldo de configuracion y monitoreo preventivo de interfaces.")

        summary_suffix = f" [{reason}]" if reason else ""
        summary = f"Diagnóstico Switch {hostname} ({vendor} - {category}): Estado {status}.{summary_suffix}"
        return {
            "summary": summary,
            "overall_status": status,
            "root_causes": root_causes,
            "action_plan": action_plan,
        }

    def _generate_local_fallback_analysis(self, data: Dict[str, Any], reason: str = "") -> Dict[str, Any]:
        """
        Rule-based heuristic fallback analysis when Gemini API is unreachable.
        Evaluates the network stack layer by layer (OSI Top-Down) and all hardware subsystems.
        """
        switch_audit = self._extract_switch_audit(data)
        if switch_audit is not None:
            return self._generate_switch_local_fallback_analysis(data, switch_audit, reason=reason)
        telemetry = data.get("telemetry", {}) if isinstance(data.get("telemetry"), dict) else {}
        osi = data.get("osi_network") or telemetry.get("osi_network") or telemetry.get("osi", {})
        osi = osi if isinstance(osi, dict) else {}
        hw = data.get("hardware") or telemetry.get("hardware", {})
        hw = hw if isinstance(hw, dict) else {}
        hw_audit = data.get("hardware_audit") or telemetry.get("hardware_audit", {})
        hw_audit = hw_audit if isinstance(hw_audit, dict) else {}
        malware_audit = data.get("malware_audit") or telemetry.get("malware_audit", {})
        malware_audit = malware_audit if isinstance(malware_audit, dict) else {}
        os_type = str(data.get("os_type", "")).upper()
        category = str(data.get("category", "")).upper()

        root_causes: List[str] = []
        action_plan: List[str] = []
        status = "OK"

        # 1. Check Hardware (CPU & RAM)
        cpu_usage = telemetry.get("cpu_percent") or telemetry.get("cpu_usage")
        if cpu_usage is None and hw_audit.get("cpu"):
            cpu_usage = hw_audit["cpu"].get("load_pct")
        if cpu_usage and isinstance(cpu_usage, (int, float)) and cpu_usage > 90:
            root_causes.append(f"[Hardware] Uso crítico de CPU al {cpu_usage}%")
            cmd = "Get-Process | Sort-Object CPU -Descending | Select-Object -First 5" if os_type == "WINDOWS" else "top -b -n 1 | head -n 15"
            action_plan.append(f"Identificar procesos saturando la CPU: {cmd}")
            status = "WARN"

        mem_usage = telemetry.get("ram_percent") or telemetry.get("ram_usage")
        if mem_usage is None and hw_audit.get("memory"):
            mem_usage = hw_audit["memory"].get("usage_pct")
        if mem_usage and isinstance(mem_usage, (int, float)) and mem_usage > 90:
            root_causes.append(f"[Hardware] Saturación de memoria RAM ({mem_usage}%)")
            action_plan.append("Reiniciar servicios o expandir memoria RAM del host")
            status = "WARN"

        # 1.1 CPU Thermals & Throttling
        cpu_info = hw_audit.get("cpu", {}) if isinstance(hw_audit.get("cpu"), dict) else {}
        cpu_temp = cpu_info.get("temp_c") or telemetry.get("cpu_temp_c")
        if cpu_temp is not None:
            try:
                temp_val = float(cpu_temp)
                if temp_val > 85:
                    root_causes.append(f"[Térmica] Temperatura crítica de CPU ({temp_val}°C): Riesgo de estrangulamiento térmico y apagado")
                    action_plan.append("Limpiar ventiladores/disipador y reemplazar pasta térmica")
                    status = "CRIT"
                elif temp_val > 75:
                    root_causes.append(f"[Térmica] Temperatura elevada en procesador ({temp_val}°C)")
                    action_plan.append("Verificar flujo de aire del chasis y carga térmica")
                    if status != "CRIT":
                        status = "WARN"
            except (ValueError, TypeError):
                pass

        # 1.2 Storage & SMART Health
        storage_info = hw_audit.get("storage", {}) if isinstance(hw_audit.get("storage"), dict) else {}
        p_drives = storage_info.get("physical_drives", []) if isinstance(storage_info.get("physical_drives"), list) else []
        for pd in p_drives:
            if isinstance(pd, dict) and pd.get("smart_fail") is True:
                d_name = pd.get("model") or pd.get("name") or "Unidad de almacenamiento"
                root_causes.append(f"[Almacenamiento] Falla predictiva SMART en disco {d_name}. Daño inminente.")
                action_plan.append(f"Respaldar datos de inmediato y sustituir la unidad de disco {d_name}.")
                status = "CRIT"

        vols = storage_info.get("volumes", []) if isinstance(storage_info.get("volumes"), list) else []
        for v in vols:
            if isinstance(v, dict):
                d_id = v.get("drive") or v.get("mount") or "Volumen"
                free_pct = v.get("free_pct")
                use_pct = v.get("use_pct")
                try:
                    if free_pct is not None:
                        f_pct = float(free_pct)
                        if f_pct < 5:
                            root_causes.append(f"[Almacenamiento] Espacio crítico en volumen {d_id} (< 5% libre)")
                            action_plan.append(f"Liberar espacio urgentemente en unidad {d_id}")
                            status = "CRIT"
                        elif f_pct < 10:
                            root_causes.append(f"[Almacenamiento] Poco espacio libre en volumen {d_id} (< 10% libre)")
                            action_plan.append(f"Limpiar archivos temporales y depurar volumen {d_id}")
                            if status != "CRIT":
                                status = "WARN"
                    elif use_pct is not None:
                        u_pct = float(str(use_pct).replace("%", ""))
                        if u_pct > 95:
                            root_causes.append(f"[Almacenamiento] Espacio crítico en montaje {d_id} ({u_pct}% en uso)")
                            action_plan.append(f"Liberar espacio en partición {d_id}")
                            status = "CRIT"
                        elif u_pct > 90:
                            root_causes.append(f"[Almacenamiento] Partición casi llena en {d_id} ({u_pct}% en uso)")
                            action_plan.append(f"Limpiar registros y archivos en {d_id}")
                            if status != "CRIT":
                                status = "WARN"
                except (ValueError, TypeError):
                    pass

        # 1.3 Battery Status
        bat_info = hw_audit.get("battery") if isinstance(hw_audit.get("battery"), dict) else None
        if bat_info and bat_info.get("present"):
            b_ch = bat_info.get("charge_pct")
            b_st = str(bat_info.get("status", "")).lower()
            try:
                if b_ch is not None and float(b_ch) < 10 and ("discharg" in b_st or "descarg" in b_st):
                    root_causes.append(f"[Energía] Batería crítica ({b_ch}%) sin conexión AC")
                    action_plan.append("Conectar el adaptador de corriente inmediatamente")
                    if status != "CRIT":
                        status = "WARN"
            except (ValueError, TypeError):
                pass

        # 2. Check OSI Layer 1 & 2 (Physical & Data Link)
        l1 = osi.get("l1_physical", {}) if isinstance(osi.get("l1_physical"), dict) else {}
        l2 = osi.get("l2_datalink", {}) if isinstance(osi.get("l2_datalink"), dict) else {}
        carrier = l1.get("carrier") or l1.get("link_carrier")
        oper_st = str(l1.get("status") or l1.get("operstate") or "").upper()
        if str(carrier) in ("0", "False", "false") or oper_st == "DOWN":
            root_causes.append("[L1 Física] Interfaz de red desconectada o cable desconectado")
            action_plan.append("Verificar conexión física del cable RJ45 o encender el adaptador de red")
            status = "CRIT"

        wifi = l2.get("wifi", {}) if isinstance(l2.get("wifi"), dict) else {}
        wifi_sig = wifi.get("signal_pct") or wifi.get("signal")
        if wifi_sig is not None:
            try:
                sig_val = int(wifi_sig)
                if sig_val < 35:
                    root_causes.append(f"[L2 Enlace] Señal Wi-Fi deficiente ({sig_val}%), alta probabilidad de pérdida de paquetes")
                    action_plan.append("Reubicar el equipo más cerca del Access Point o cambiar a banda 5 GHz")
                    if status != "CRIT":
                        status = "WARN"
            except (ValueError, TypeError):
                pass

        # 3. Check OSI Layer 3 (Network, IP, Gateway, Ping)
        l3 = osi.get("l3_network", {}) if isinstance(osi.get("l3_network"), dict) else {}
        ip_addr = str(l3.get("ip") or telemetry.get("ip") or "")
        if "169.254." in ip_addr:
            root_causes.append("[L3 Red] Dirección APIPA (169.254.x.x): Falla de negociación DHCP")
            dhcp_cmd = "ipconfig /renew" if os_type == "WINDOWS" else "sudo dhclient -r && sudo dhclient"
            action_plan.append(f"Renovar concesión DHCP: {dhcp_cmd} o revisar servidor DHCP")
            status = "CRIT"

        ping_gw = l3.get("ping_gateway") if "ping_gateway" in l3 else telemetry.get("ping_gateway")
        if ping_gw is False:
            root_causes.append("[L3 Red] Puerta de enlace (Default Gateway) no responde a ping ICMP")
            gw_cmd = "Test-Connection (Get-NetRoute -DestinationPrefix '0.0.0.0/0').NextHop" if os_type == "WINDOWS" else "ip route show default"
            action_plan.append(f"Verificar ruta y enlace hacia Gateway: {gw_cmd}")
            status = "CRIT"

        ping_ext = l3.get("ping_internet") if "ping_internet" in l3 else telemetry.get("ping_internet")
        if ping_gw is True and ping_ext is False:
            root_causes.append("[L3 Red] Gateway responde pero no hay salida a Internet (8.8.8.8)")
            action_plan.append("Revisar conexión WAN del router o estado del enlace del ISP")
            status = "CRIT"

        # 4. Check OSI Layer 7 (Application: DNS & Captive Portal)
        l7 = osi.get("l7_application", {}) if isinstance(osi.get("l7_application"), dict) else {}
        captive = l7.get("captive_portal") or l7.get("captive_portal_detected")
        if captive:
            root_causes.append("[L7 Aplicación] Portal cautivo detectado bloqueando tráfico web hacia Internet")
            action_plan.append("Abrir navegador web en el host para autenticarse en el portal de red")
            if status != "CRIT":
                status = "WARN"

        dns_ok = l7.get("dns_ok") if "dns_ok" in l7 else l7.get("dns_resolution_ok")
        if dns_ok is False:
            if ping_ext is True:
                root_causes.append("[L7 Aplicación] Falla de resolución DNS mientras la conectividad IP externa funciona")
                dns_cmd = "Set-DnsClientServerAddress -InterfaceAlias '*' -ServerAddresses 8.8.8.8,1.1.1.1" if os_type == "WINDOWS" else "echo 'nameserver 8.8.8.8' | sudo tee /etc/resolv.conf"
                action_plan.append(f"Cambiar servidores DNS a públicos (8.8.8.8 / 1.1.1.1): {dns_cmd}")
            else:
                root_causes.append("[L7 Aplicación] Falla de resolución DNS por falta de salida a red")
                action_plan.append("Resolver primero la conectividad de Capa 3 antes del servicio de nombres")
            status = "CRIT"

        # 5. Check Malware / Threat Analysis
        is_malware_scan = bool("MALWARE" in category or "VIRUS" in category or malware_audit)
        defs = malware_audit.get("defenses", {}) if isinstance(malware_audit.get("defenses"), dict) else {}
        av_name = defs.get("antivirus_name") or telemetry.get("antivirus_enabled")
        av_act = defs.get("antivirus_active") if "antivirus_active" in defs else telemetry.get("antivirus_active")

        if is_malware_scan:
            if av_act is False or (av_name is not None and str(av_name).strip() in ("Ninguno", "None", "")):
                root_causes.append("[Seguridad] Protección antivirus desactivada o ausente en el endpoint")
                av_cmd = "Set-MpPreference -DisableRealtimeMonitoring $false" if os_type == "WINDOWS" else "sudo systemctl start clamav-daemon"
                action_plan.append(f"Activar de inmediato la protección en tiempo real: {av_cmd}")
                status = "CRIT"
        else:
            if av_act is False or telemetry.get("antivirus_enabled") is False:
                root_causes.append("[Seguridad] Protección antivirus desactivada en el host")
                action_plan.append("Habilitar Windows Defender o software antivirus corporativo")
                status = "CRIT"

        fw_enabled = defs.get("firewall_enabled")
        if fw_enabled is False:
            root_causes.append("[Seguridad] Cortafuegos (Firewall) deshabilitado en el endpoint")
            fw_cmd = "Set-NetFirewallProfile -Profile Domain,Public,Private -Enabled True" if os_type == "WINDOWS" else "sudo ufw enable"
            action_plan.append(f"Habilitar cortafuegos local: {fw_cmd}")
            if status != "CRIT":
                status = "WARN"

        susp_procs = malware_audit.get("suspicious_processes", []) if isinstance(malware_audit.get("suspicious_processes"), list) else []
        for sp in susp_procs:
            if isinstance(sp, dict):
                p_name = sp.get("name") or "proceso"
                p_pid = sp.get("pid") or "?"
                p_rsn = sp.get("reason") or "Ubicación anómala"
                root_causes.append(f"[Malware/Procesos] Proceso sospechoso en ejecución: {p_name} (PID {p_pid}) - {p_rsn}")
                kill_cmd = f"Stop-Process -Id {p_pid} -Force" if os_type == "WINDOWS" else f"kill -9 {p_pid}"
                action_plan.append(f"Aislar y terminar proceso anómalo {p_name} (PID {p_pid}): {kill_cmd}")
                status = "CRIT"

        net_c2 = malware_audit.get("network_c2", {}) if isinstance(malware_audit.get("network_c2"), dict) else {}
        susp_conns = [c for c in net_c2.get("established_connections", []) if isinstance(c, dict) and c.get("suspicious")]
        for sc in susp_conns:
            rip = sc.get("remote_ip")
            rport = sc.get("remote_port")
            p_own = sc.get("process") or "desconocido"
            root_causes.append(f"[Malware/C2] Conexión establecida a socket C2 sospechoso: {rip}:{rport} (Proceso: {p_own})")
            action_plan.append(f"Bloquear tráfico hacia {rip}:{rport} en el firewall perimetral y desconectar equipo de la red")
            status = "CRIT"

        if net_c2.get("hosts_file_hijack") is True:
            root_causes.append("[Malware/Hosts] Archivo hosts alterado: Redirección maliciosa de dominios de antivirus o repositorios")
            h_cmd = "notepad C:\\Windows\\System32\\drivers\\etc\\hosts" if os_type == "WINDOWS" else "sudo nano /etc/hosts"
            action_plan.append(f"Restaurar archivo hosts original y depurar entradas ilegítimas: {h_cmd}")
            status = "CRIT"

        recent_arts = malware_audit.get("recent_artifacts", []) if isinstance(malware_audit.get("recent_artifacts"), list) else []
        if recent_arts:
            root_causes.append(f"[Malware/Staging] Detectados {len(recent_arts)} ejecutables/scripts recientes creados en carpetas temporales (<7 días)")
            action_plan.append("Inspeccionar y eliminar binarios staging sospechosos en directorios temporales")
            if status != "CRIT":
                status = "WARN"

        if not root_causes:
            if "MALWARE" in category or "VIRUS" in category:
                root_causes.append("Auditoría de seguridad nominal. No se detectaron indicios de malware, procesos anómalos ni sockets C2.")
                action_plan.append("Mantener definiciones antivirus actualizadas y realizar análisis periódicos.")
            elif "HARDWARE" in category or "CPU" in category:
                root_causes.append("Subsistemas de hardware nominales (CPU, RAM, Discos y Firmware).")
                action_plan.append("Mantener monitoreo preventivo y ventilación adecuada.")
            else:
                root_causes.append("Pila de red nominal (Capa 7 a Capa 1). No se detectaron anomalías.")
                action_plan.append("Mantener monitoreo preventivo y parches de seguridad al día.")

        summary_suffix = f" [{reason}]" if reason else ""
        summary = f"Diagnóstico {category} ({os_type}): Estado general {status}.{summary_suffix}"

        return {
            "summary": summary,
            "overall_status": status,
            "root_causes": root_causes,
            "action_plan": action_plan,
        }
