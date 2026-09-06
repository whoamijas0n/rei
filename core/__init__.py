"""
REI - Core Module
Decoupled diagnostic execution, plugin interfaces, and data parsing.
"""

from .interfaces import (
    IDiagnosticPlugin,
    DiagnosticResult,
    DiagnosticStatus,
    Severity,
    AIAnalysisResult,
    EndpointDiagnosticData,
    NetworkSwitchDiagnosticData,
)
from .manager import DiagnosticManager
from .parser import MetricParser
from .hid_injector import USBHIDInjector
from .serial_receiver import USBSerialReceiver
from .switch_serial import SwitchSerialHandler
from .ai_client import DiagnosticAnalyzer
from .plugins import (
    WindowsDiagnosticPlugin,
    LinuxDiagnosticPlugin,
    SwitchDiagnosticPlugin,
    PoweroffPlugin,
    RebootPlugin,
    execute_system_poweroff,
    execute_system_reboot,
)

__all__ = [
    "IDiagnosticPlugin",
    "DiagnosticResult",
    "DiagnosticStatus",
    "Severity",
    "AIAnalysisResult",
    "EndpointDiagnosticData",
    "NetworkSwitchDiagnosticData",
    "DiagnosticManager",
    "MetricParser",
    "USBHIDInjector",
    "USBSerialReceiver",
    "SwitchSerialHandler",
    "DiagnosticAnalyzer",
    "WindowsDiagnosticPlugin",
    "LinuxDiagnosticPlugin",
    "SwitchDiagnosticPlugin",
    "PoweroffPlugin",
    "RebootPlugin",
    "execute_system_poweroff",
    "execute_system_reboot",
]

