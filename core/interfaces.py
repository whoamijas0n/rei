"""
REI - Core Interfaces
Defines abstract plugin contracts and diagnostic data structures.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional
import time


class DiagnosticStatus(Enum):
    """Execution status states for decoupled diagnostic tasks."""
    IDLE = auto()
    PENDING = auto()
    RUNNING = auto()
    SUCCESS = auto()
    FAILED = auto()
    WARNING = auto()


@dataclass
class DiagnosticResult:
    """Encapsulates the output of a diagnostic plugin execution."""
    plugin_name: str
    status: DiagnosticStatus = DiagnosticStatus.IDLE
    summary: str = ""
    details: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
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
