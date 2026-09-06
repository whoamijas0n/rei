"""
REI - Core Interfaces
Defines abstract plugin contracts and diagnostic data structures.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional
import time


class Severity(str, Enum):
    """Normalized diagnostic severity levels for endpoints and network gear."""
    SALUDABLE = "SALUDABLE"
    ADVERTENCIA = "ADVERTENCIA"
    CRITICO = "CRÍTICO"


class DiagnosticStatus(Enum):
    """Execution status states for decoupled diagnostic tasks."""
    IDLE = auto()
    PENDING = auto()
    RUNNING = auto()
    SUCCESS = auto()
    FAILED = auto()
    WARNING = auto()


@dataclass
class AIAnalysisResult:
    """Structured output from Google Gemini AI or local heuristic fallback engine."""
    estado: Severity
    diagnostico_corto: str  # Max 2 lines formatted for 128x64 OLED (<= 42 chars)
    acciones_recomendadas: List[str] = field(default_factory=list)  # 2-3 concrete actionable steps
    confianza: float = 1.0
    origen: str = "HEURISTIC"  # "GEMINI_AI" or "HEURISTIC"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "estado": self.estado.value,
            "diagnostico_corto": self.diagnostico_corto,
            "acciones_recomendadas": self.acciones_recomendadas,
            "confianza": self.confianza,
            "origen": self.origen,
            "timestamp": self.timestamp,
        }


@dataclass
class StorageDriveInfo:
    """Physical drive information and SMART telemetry."""
    device: str
    size_gb: float = 0.0
    free_gb: float = 0.0
    health_status: str = "OK"  # "OK", "PREDICT_FAILURE", "DEGRADED", "UNKNOWN"
    smart_alerts: List[str] = field(default_factory=list)


@dataclass
class EndpointDiagnosticData:
    """Hardware and OS diagnostic telemetry exfiltrated from Windows/Linux endpoint."""
    os_type: str  # "windows" or "linux"
    os_version: str = ""
    hostname: str = ""
    uptime_seconds: float = 0.0
    cpu_model: str = ""
    cpu_load_pct: float = 0.0
    ram_total_mb: float = 0.0
    ram_used_mb: float = 0.0
    ram_free_mb: float = 0.0
    drives: List[StorageDriveInfo] = field(default_factory=list)
    critical_events: List[str] = field(default_factory=list)  # BSOD/Kernel Panics/MCE/EventLog
    network_adapters: List[Dict[str, str]] = field(default_factory=list)
    raw_payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class NetworkSwitchDiagnosticData:
    """Audit and health telemetry extracted from switch/router serial console."""
    hostname: str = ""
    model: str = ""
    ios_version: str = ""
    uptime: str = ""
    total_ports: int = 0
    ports_up: int = 0
    ports_down: int = 0
    ports_err_disabled: List[str] = field(default_factory=list)
    ports_with_crc_errors: Dict[str, int] = field(default_factory=dict)  # port -> error_count
    power_supplies: Dict[str, str] = field(default_factory=dict)  # "PS1": "OK", "PS2": "FAULT"
    temperature_status: str = "OK"
    critical_syslog: List[str] = field(default_factory=list)
    raw_commands: Dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class DiagnosticResult:
    """Encapsulates the output of a diagnostic plugin execution."""
    plugin_name: str
    status: DiagnosticStatus = DiagnosticStatus.IDLE
    summary: str = ""
    details: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    ai_analysis: Optional[AIAnalysisResult] = None
    severity: Severity = Severity.SALUDABLE
    timestamp: float = field(default_factory=time.time)
    elapsed_seconds: float = 0.0

    @property
    def is_finished(self) -> bool:
        """Returns True if the task has concluded execution."""
        return self.status in (
            DiagnosticStatus.SUCCESS,
            DiagnosticStatus.FAILED,
            DiagnosticStatus.WARNING,
        )


class IDiagnosticPlugin(ABC):
    """Abstract Base Class for all decoupled diagnostic plugins."""

    @property
    @abstractmethod
    def id(self) -> str:
        """Unique identifier for the plugin."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable display name for the plugin."""
        pass

    @property
    @abstractmethod
    def category(self) -> str:
        """Category (e.g., 'SYSTEM', 'NETWORK', 'ENDPOINTS', 'SWITCHES', 'VAULT')."""
        pass

    @abstractmethod
    def run(self, **kwargs) -> DiagnosticResult:
        """
        Executes the diagnostic operation.
        Must be thread-safe and non-blocking to the main UI thread.
        """
        pass

