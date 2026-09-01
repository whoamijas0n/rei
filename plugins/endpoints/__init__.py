"""
REI - Endpoint PC Diagnostic Plugins Package (USB HID Rubber Ducky)
"""

from .hid_windows import WindowsHIDPlugin, WindowsPayloadGenerator
from .hid_linux import LinuxHIDPlugin, LinuxPayloadGenerator

__all__ = [
    "WindowsHIDPlugin",
    "WindowsPayloadGenerator",
    "LinuxHIDPlugin",
    "LinuxPayloadGenerator",
]
