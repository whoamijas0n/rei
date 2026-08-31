"""
REI - Metric and Output Parser
Utilities for parsing network, hardware, and system telemetry data.
"""

import re
from typing import Dict, List, Any


class MetricParser:
    """Parses raw shell and hardware output into formatted UI metric dictionaries."""

    @staticmethod
    def parse_ip_output(raw_output: str) -> List[Dict[str, str]]:
        """Parses `ip addr` or `ifconfig` output into interface/IP mappings."""
        interfaces = []
        # Match standard Linux ip -brief addr or ifconfig patterns
        for line in raw_output.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 3:
                iface = parts[0]
                status = parts[1]
                ip = parts[2].split("/")[0]
                interfaces.append({"iface": iface, "status": status, "ip": ip})
        return interfaces

    @staticmethod
    def parse_wifi_scan(raw_output: str) -> List[Dict[str, Any]]:
        """Parses `nmcli` or `iwlist` scan outputs into clean SSID lists."""
        networks = []
        for line in raw_output.splitlines():
            line = line.strip()
            if not line:
                continue
            # Generic parser for SSID and Signal
            match = re.search(r"SSID:\s*(.+?)(?:\s+SIGNAL:\s*(\d+)%)?", line, re.IGNORECASE)
            if match:
                networks.append({
                    "ssid": match.group(1).strip(),
                    "signal": int(match.group(2)) if match.group(2) else 100
                })
        return networks

    @staticmethod
    def format_bytes(bytes_val: int) -> str:
        """Converts raw byte counts to human readable strings."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes_val < 1024.0:
                return f"{bytes_val:.1f} {unit}"
            bytes_val /= 1024.0
        return f"{bytes_val:.1f} TB"
