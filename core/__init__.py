"""
REI - Core Module
Decoupled diagnostic execution, plugin interfaces, and data parsing.
"""

from .interfaces import IDiagnosticPlugin, DiagnosticResult, DiagnosticStatus
from .manager import DiagnosticManager
from .parser import MetricParser
from .plugins import (
    PoweroffPlugin,
    RebootPlugin,
    execute_system_poweroff,
    execute_system_reboot,
)

__all__ = [
    "IDiagnosticPlugin",
    "DiagnosticResult",
    "DiagnosticStatus",
    "DiagnosticManager",
    "MetricParser",
    "PoweroffPlugin",
    "RebootPlugin",
    "execute_system_poweroff",
    "execute_system_reboot",
]
