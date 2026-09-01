"""
REI - Core Interfaces
Defines abstract plugin contracts, severity levels, and diagnostic data structures.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Union
import time


class Severity(Enum):
    """Diagnostic severity levels for metrics and consolidated results."""
    OK = "OK"          # Nominal state [✓]
    INFO = "INFO"      # Informational notification [i]
    WARNING = "WARN"   # Warning / Threshold exceeded [!]
    CRITICAL = "CRIT"  # Critical failure / Hardware at risk [x]


class DiagnosticStatus(Enum):
    """Execution lifecycle status for decoupled diagnostic tasks."""
    IDLE = auto()
    PENDING = auto()
    RUNNING = auto()
    SUCCESS = auto()
    FAILED = auto()
    WARNING = auto()


@dataclass
class DiagnosticMetric:
    """Individual diagnostic metric collected during audit."""
    name: str                        # e.g., "Latencia Gateway", "Temp CPU", "Amenazas"
    value: str                       # e.g., "12ms (OK)", "88°C (ALERTA)", "0 detectadas"
    status: Severity = Severity.OK   # Individual metric severity
    details: Optional[str] = None    # Contextual explanation or remediation tip


@dataclass
class DiagnosticResult:
    """Encapsulates the complete output of a diagnostic plugin execution."""
    plugin_name: str
    target_identifier: str = ""
    execution_time_ms: int = 0
    status: DiagnosticStatus = DiagnosticStatus.IDLE
    overall_status: Severity = Severity.OK
    summary: str = ""
    details: List[str] = field(default_factory=list)
    metrics: Union[List[DiagnosticMetric], Dict[str, Any]] = field(default_factory=list)
    raw_output: Optional[str] = None
    ai_analysis: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
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
        """Category (e.g., 'SYSTEM', 'NETWORK', 'ENDPOINTS', 'VAULT')."""
        pass

    @abstractmethod
    def run(self, **kwargs) -> DiagnosticResult:
        """
        Executes the diagnostic operation.
        Must be thread-safe and non-blocking to the main UI thread.
        """
        pass
