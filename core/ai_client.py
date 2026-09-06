"""
REI - AI Diagnostic Client & Offline Heuristics Engine
Integrates Google Gemini REST API (<8s timeout) with a deterministic offline heuristic rule engine.
Produces structured diagnostic results with Severity, 2-line OLED summary, and recommended steps.
Persists full audit reports locally under data/reports/.
"""

import json
import logging
import os
import re
import socket
import time
from typing import Any, Dict, List, Optional, Union

try:
    import requests
except ImportError:
    requests = None

from .interfaces import (
    AIAnalysisResult,
    EndpointDiagnosticData,
    NetworkSwitchDiagnosticData,
    Severity,
)

logger = logging.getLogger("REI.Core.AIClient")


class DiagnosticAnalyzer:
    """
    Orchestrates automated analysis for PC endpoints and network switches.
    Prioritizes Google Gemini REST API; seamlessly falls back to the offline rule engine.
    """

    DEFAULT_MODEL = "gemini-1.5-flash"
    DEFAULT_TIMEOUT_SEC = 8.0
    REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "reports")

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_SEC,
    ):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        self.model = model or os.getenv("GEMINI_MODEL", self.DEFAULT_MODEL)
        self.timeout = timeout
        os.makedirs(self.REPORTS_DIR, exist_ok=True)

    def is_internet_available(self) -> bool:
        """Rapid DNS/socket probe to verify internet connectivity."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(1.5)
                s.connect(("8.8.8.8", 80))
            return True
        except Exception:
            return False

    def analyze_endpoint(self, data: EndpointDiagnosticData) -> AIAnalysisResult:
        """Analyzes PC endpoint telemetry (Windows or Linux)."""
        result = None
        # Attempt Gemini analysis if API key is present and internet is up
        if self.api_key and requests and self.is_internet_available():
            try:
                result = self._gemini_analyze_endpoint(data)
            except Exception as ex:
                logger.warning(f"Gemini API endpoint analysis failed: {ex}. Using local heuristics.")

        if not result:
            result = self.heuristic_analyze_endpoint(data)

        self._save_report("endpoint", data.hostname or "pc", data.raw_payload or data.__dict__, result)
        return result

    def analyze_switch(self, data: NetworkSwitchDiagnosticData) -> AIAnalysisResult:
        """Analyzes Network Switch/Router serial console telemetry."""
        result = None
        if self.api_key and requests and self.is_internet_available():
            try:
                result = self._gemini_analyze_switch(data)
            except Exception as ex:
                logger.warning(f"Gemini API switch analysis failed: {ex}. Using local heuristics.")

        if not result:
            result = self.heuristic_analyze_switch(data)

        self._save_report("switch", data.hostname or "switch", data.raw_commands or data.__dict__, result)
        return result

    # -------------------------------------------------------------------------
    # OFFLINE HEURISTIC RULE ENGINE (100% Deterministic & Autonomous)
    # -------------------------------------------------------------------------

    def heuristic_analyze_endpoint(self, data: EndpointDiagnosticData) -> AIAnalysisResult:
        """
        Deterministic diagnostic rules for Windows and Linux endpoints:
        - RAM > 90% (Critical) / RAM > 80% (Warning)
        - SMART Predictive Failure (Critical) / Disk Free < 10% (Warning)
        - Critical OS Events (BSOD, Kernel Panic, Machine Check Exception) (Critical)
        """
        issues: List[str] = []
        actions: List[str] = []
        severity = Severity.SALUDABLE

        # 1. RAM Utilization Check
        if data.ram_total_mb > 0:
            ram_pct = (data.ram_used_mb / data.ram_total_mb) * 100.0
            if ram_pct >= 90.0:
                severity = Severity.CRITICO
                issues.append(f"RAM saturada ({int(ram_pct)}%)")
                actions.append("Cerrar apps en segundo plano")
            elif ram_pct >= 80.0 and severity != Severity.CRITICO:
                severity = Severity.ADVERTENCIA
                issues.append(f"RAM elevada ({int(ram_pct)}%)")
                actions.append("Monitorear consumo de memoria")

        # 2. Storage & SMART Health Check
        for drive in data.drives:
            if drive.health_status in ("PREDICT_FAILURE", "DEGRADED") or drive.smart_alerts:
                severity = Severity.CRITICO
                issues.append(f"Fallo SMART en {drive.device}")
                actions.append(f"Respaldar datos de {drive.device} de inmediato")
                actions.append(f"Reemplazar disco {drive.device}")
            elif drive.size_gb > 0 and (drive.free_gb / drive.size_gb) < 0.10:
                if severity != Severity.CRITICO:
                    severity = Severity.ADVERTENCIA
                issues.append(f"Espacio bajo en {drive.device}")
                actions.append(f"Liberar espacio en {drive.device}")

        # 3. Critical System Events & BSODs
        if data.critical_events:
            for ev in data.critical_events:
                ev_str = str(ev).lower()
                if any(k in ev_str for k in ["id 41", "id 1001", "id 6008", "kernel-power", "panic", "mce", "oops"]):
                    severity = Severity.CRITICO
                    issues.append("Reinicio abrupto / BSOD")
                    actions.append("Revisar logs de energía y RAM")
                    break
                elif any(k in ev_str for k in ["failed systemd", "error"]):
                    if severity != Severity.CRITICO:
                        severity = Severity.ADVERTENCIA
                    issues.append("Servicios con fallas")
                    actions.append("Verificar systemctl --failed")

        # 4. Synthesize OLED 2-line summary
        if severity == Severity.SALUDABLE:
            short_diag = f"Sistema {data.os_type.capitalize()} OK\nHardware en buen estado"
            actions = ["Mantener parches al día", "Uso regular normal"]
        else:
            line1 = issues[0][:20] if issues else "Anomalías detectadas"
            line2 = issues[1][:20] if len(issues) > 1 else ("Atención requerida" if severity == Severity.ADVERTENCIA else "Falla crítica detectada")
            short_diag = f"{line1}\n{line2}"

        return AIAnalysisResult(
            estado=severity,
            diagnostico_corto=short_diag,
            acciones_recomendadas=actions[:3],
            confianza=0.95,
            origen="HEURISTIC",
            timestamp=time.time(),
        )

    def heuristic_analyze_switch(self, data: NetworkSwitchDiagnosticData) -> AIAnalysisResult:
        """
        Deterministic diagnostic rules for Network Switches/Routers:
        - Power Supply Fault (Critical)
        - Err-disabled ports (Warning / Critical)
        - High CRC errors on ports (Warning)
        - Syslog alarms (Warning)
        """
        issues: List[str] = []
        actions: List[str] = []
        severity = Severity.SALUDABLE

        # 1. Power Supply Check
        for psu, status in data.power_supplies.items():
            if status.upper() in ("FAULT", "FAIL", "BAD", "OFFLINE"):
                severity = Severity.CRITICO
                issues.append(f"Falla {psu}")
                actions.append(f"Reemplazar módulo {psu}")
                actions.append("Verificar alimentación AC/DC")

        # 2. Err-disabled Ports
        if data.ports_err_disabled:
            if severity != Severity.CRITICO:
                severity = Severity.ADVERTENCIA
            p_list = ",".join(data.ports_err_disabled[:2])
            issues.append(f"Err-disable: {p_list}")
            actions.append(f"Revisar loop/BPDU en {p_list}")
            actions.append("Ejecutar shutdown / no shutdown")

        # 3. High CRC Error Counts
        high_crc_ports = [p for p, count in data.ports_with_crc_errors.items() if count >= 10]
        if high_crc_ports:
            if severity != Severity.CRITICO:
                severity = Severity.ADVERTENCIA
            issues.append(f"CRC errors en {len(high_crc_ports)} pto(s)")
            actions.append("Revisar o cambiar cable patch")
            actions.append("Inspeccionar transceptor SFP")

        # 4. Critical Syslog
        if data.critical_syslog and severity == Severity.SALUDABLE:
            severity = Severity.ADVERTENCIA
            issues.append("Alarmas en Syslog")
            actions.append("Consultar show logging")

        # 5. Synthesize OLED 2-line summary
        if severity == Severity.SALUDABLE:
            short_diag = f"Switch {data.hostname[:12]} OK\nPuertos y fuentes OK"
            actions = ["Operación normal", "Respaldar running-config"]
        else:
            line1 = issues[0][:20] if issues else "Alerta de red"
            line2 = issues[1][:20] if len(issues) > 1 else ("Atención preventiva" if severity == Severity.ADVERTENCIA else "Falla crítica en equipo")
            short_diag = f"{line1}\n{line2}"

        return AIAnalysisResult(
            estado=severity,
            diagnostico_corto=short_diag,
            acciones_recomendadas=actions[:3],
            confianza=0.95,
            origen="HEURISTIC",
            timestamp=time.time(),
        )

    # -------------------------------------------------------------------------
    # GOOGLE GEMINI REST CLIENT
    # -------------------------------------------------------------------------

    def _gemini_analyze_endpoint(self, data: EndpointDiagnosticData) -> AIAnalysisResult:
        """Queries Google Gemini REST API to analyze PC endpoint telemetry."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"

        prompt = f"""
Actúa como un experto en diagnóstico de sistemas embebidos y hardware.
Analiza la siguiente telemetría de una computadora ({data.os_type}) y genera un diagnóstico conciso.

Telemetría:
- SO: {data.os_version} (Host: {data.hostname}, Uptime: {int(data.uptime_seconds)}s)
- CPU: {data.cpu_model} (Carga: {data.cpu_load_pct}%)
- RAM: {data.ram_used_mb:.0f}MB / {data.ram_total_mb:.0f}MB ({((data.ram_used_mb/max(1,data.ram_total_mb))*100):.1f}%)
- Discos: {[d.__dict__ for d in data.drives]}
- Eventos Críticos: {data.critical_events}

REQUISITOS ESTRICTOS:
1. Responde ÚNICAMENTE un JSON válido sin markdown, sin backticks (```).
2. 'estado' debe ser uno de: "SALUDABLE", "ADVERTENCIA", "CRÍTICO".
3. 'diagnostico_corto' debe tener EXACTAMENTE dos líneas separadas por '\\n', máximo 20 caracteres por línea (para pantalla OLED 128x64).
4. 'acciones_recomendadas' debe ser una lista de 2 a 3 acciones técnicas concretas (máximo 32 caracteres cada una).

Esquema JSON:
{{
  "estado": "SALUDABLE" | "ADVERTENCIA" | "CRÍTICO",
  "diagnostico_corto": "Línea 1 (<=20 car)\\nLínea 2 (<=20 car)",
  "acciones_recomendadas": ["Paso 1", "Paso 2"],
  "confianza": 0.98
}}
"""

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 300,
                "responseMimeType": "application/json",
            }
        }

        resp = requests.post(url, json=payload, timeout=self.timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini API error ({resp.status_code}): {resp.text}")

        resp_json = resp.json()
        raw_text = resp_json["candidates"][0]["content"]["parts"][0]["text"].strip()

        clean_text = re.sub(r"^```(?:json)?", "", raw_text, flags=re.IGNORECASE).rstrip("`").strip()
        parsed = json.loads(clean_text)

        raw_state = parsed.get("estado", "SALUDABLE").upper()
        if "CRIT" in raw_state:
            state = Severity.CRITICO
        elif "ADV" in raw_state or "WARN" in raw_state:
            state = Severity.ADVERTENCIA
        else:
            state = Severity.SALUDABLE

        return AIAnalysisResult(
            estado=state,
            diagnostico_corto=parsed.get("diagnostico_corto", "Análisis OK"),
            acciones_recomendadas=parsed.get("acciones_recomendadas", []),
            confianza=float(parsed.get("confianza", 0.98)),
            origen="GEMINI_AI",
            timestamp=time.time(),
        )

    def _gemini_analyze_switch(self, data: NetworkSwitchDiagnosticData) -> AIAnalysisResult:
        """Queries Google Gemini REST API to analyze switch/router telemetry."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"

        prompt = f"""
Actúa como un Ingeniero Senior CCIE en redes Cisco.
Analiza la telemetría de consola serial de este switch/router:
- Hostname: {data.hostname} (Modelo: {data.model}, IOS: {data.ios_version})
- Uptime: {data.uptime}
- Puertos UP/DOWN: {data.ports_up}/{data.ports_down} (Total: {data.total_ports})
- Puertos Err-Disabled: {data.ports_err_disabled}
- Puertos con CRC Errors: {data.ports_with_crc_errors}
- Fuentes de Poder: {data.power_supplies}
- Syslog Crítico: {data.critical_syslog}

REQUISITOS ESTRICTOS:
1. Responde ÚNICAMENTE un JSON válido sin bloques de código markdown.
2. 'estado': "SALUDABLE", "ADVERTENCIA", o "CRÍTICO".
3. 'diagnostico_corto': EXACTAMENTE dos líneas separadas por '\\n', máximo 20 caracteres por línea.
4. 'acciones_recomendadas': 2 a 3 acciones concretas.

Esquema:
{{
  "estado": "SALUDABLE" | "ADVERTENCIA" | "CRÍTICO",
  "diagnostico_corto": "Línea 1\\nLínea 2",
  "acciones_recomendadas": ["Paso 1", "Paso 2"],
  "confianza": 0.98
}}
"""

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 300,
                "responseMimeType": "application/json",
            }
        }

        resp = requests.post(url, json=payload, timeout=self.timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini API error ({resp.status_code}): {resp.text}")

        resp_json = resp.json()
        raw_text = resp_json["candidates"][0]["content"]["parts"][0]["text"].strip()
        clean_text = re.sub(r"^```(?:json)?", "", raw_text, flags=re.IGNORECASE).rstrip("`").strip()
        parsed = json.loads(clean_text)

        raw_state = parsed.get("estado", "SALUDABLE").upper()
        if "CRIT" in raw_state:
            state = Severity.CRITICO
        elif "ADV" in raw_state or "WARN" in raw_state:
            state = Severity.ADVERTENCIA
        else:
            state = Severity.SALUDABLE

        return AIAnalysisResult(
            estado=state,
            diagnostico_corto=parsed.get("diagnostico_corto", "Switch Audit OK"),
            acciones_recomendadas=parsed.get("acciones_recomendadas", []),
            confianza=float(parsed.get("confianza", 0.98)),
            origen="GEMINI_AI",
            timestamp=time.time(),
        )

    def _save_report(
        self,
        report_type: str,
        target_name: str,
        raw_data: Any,
        analysis: AIAnalysisResult,
    ) -> None:
        """Persists audit report as timestamped JSON under data/reports/."""
        try:
            ts_str = time.strftime("%Y%m%d_%H%M%S")
            safe_target = re.sub(r"[^A-Za-z0-9_\-]", "_", target_name)
            filename = f"report_{ts_str}_{report_type}_{safe_target}.json"
            filepath = os.path.join(self.REPORTS_DIR, filename)

            content = {
                "timestamp": time.time(),
                "datetime": time.strftime("%Y-%m-%d %H:%M:%S"),
                "report_type": report_type,
                "target": target_name,
                "analysis": analysis.to_dict(),
                "telemetry": raw_data if isinstance(raw_data, dict) else str(raw_data),
            }

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(content, f, indent=2, ensure_ascii=False, default=str)

            logger.info(f"Saved diagnostic report to: {filepath}")

        except Exception as ex:
            logger.error(f"Failed to persist report to disk: {ex}")
