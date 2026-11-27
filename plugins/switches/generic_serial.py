"""
REI - Perfil Genérico Seguro de Respaldo (plugins/switches/generic_serial.py)

Captura información de solo lectura básica cuando el switch no coincide con un
fabricante conocido (ej. banner corporativo personalizado o marca aún sin perfil
dedicado como Samsung/Allied/D-Link) para entregarla al reporte HTML y a Gemini.
"""

from typing import Any, Dict, List, Optional, Tuple

from .base import (
    ISwitchProfile,
    SwitchAuditOutcome,
    SwitchCommand,
    get_switch_category_label,
    normalize_switch_category,
)


_GENERIC_GENERAL_COMMANDS: Tuple[SwitchCommand, ...] = (
    SwitchCommand("version", "show version", "Consultando version..."),
    SwitchCommand("interfaces_status", "show interfaces status", "Consultando puertos..."),
    SwitchCommand("vlans", "show vlan", "Consultando VLANs..."),
)


class GenericSerialProfile(ISwitchProfile):
    """Perfil de respaldo de solo lectura para switches no reconocidos."""

    @property
    def vendor_id(self) -> str:
        return "generic"

    @property
    def display_name(self) -> str:
        return "GENERICO"

    @property
    def netmiko_device_type(self) -> str:
        return "generic_termserver_serial"

    @property
    def preferred_baudrates(self) -> Tuple[int, ...]:
        return (9600, 115200, 38400)

    def match_passive(self, banner: str, prompt: str = "") -> float:
        return 0.05 if (banner or prompt) else 0.0

    def match_probe_output(self, output: str) -> float:
        return 0.05 if (output or "").strip() else 0.0

    def get_commands(self, category: str = "GENERAL", is_privileged: bool = True) -> List[SwitchCommand]:
        return list(_GENERIC_GENERAL_COMMANDS)

    def parse_outputs(
        self,
        command_outputs: Dict[str, str],
        context: Optional[Dict[str, Any]] = None,
    ) -> SwitchAuditOutcome:
        ctx = context or {}
        norm_cat = normalize_switch_category(ctx.get("category"))
        hostname = ctx.get("hostname_hint") or "Switch-Generico"
        port_dev = ctx.get("port_device", "/dev/ttyUSB0")
        baudrate = int(ctx.get("baudrate", 9600))

        switch_audit: Dict[str, Any] = {
            "vendor_id": self.vendor_id,
            "vendor_name": self.display_name,
            "category": norm_cat,
            "category_label": get_switch_category_label(norm_cat),
            "connection": {
                "port": port_dev,
                "port_kind": ctx.get("port_kind", "USB_RJ45"),
                "baudrate": baudrate,
                "privileged": bool(ctx.get("is_privileged", False)),
                "signals": ctx.get("signals", {}),
            },
            "identity": {
                "hostname": hostname,
                "model": "Switch CLI Generico",
                "serial_number": "",
                "os_version": "Desconocida",
                "uptime": "",
                "reload_reason": "",
                "stack_count": 1,
                "management_ip": "N/D",
                "management_interfaces": [],
            },
            "health": {"cpu": {}, "memory": {}, "environment": {}},
            "poe": {"supported": False},
            "interfaces": {"counts": {}, "uplinks_10g": [], "half_duplex_ports": [], "err_disabled": [], "error_counters": []},
            "switching": {"vlans": {"count": 0, "vlans": []}, "stp": {}, "neighbors": []},
            "logging": {"recent_lines": [], "critical_events": [], "total_captured": 0},
            "findings": [],
            "skipped_privileged": list(ctx.get("skipped_privileged", [])),
            "raw_commands": command_outputs,
        }

        details = [
            f"Host: {hostname[:15]}",
            f"Puerto: {port_dev.replace('/dev/', '')}",
            f"Baud: {baudrate} 8-N-1",
            "Salida capturada (Generico)",
        ]
        detail_pages = [
            {
                "title": "CONSOLA SERIAL",
                "hero": {"tag": "SW", "value": hostname[:14]},
                "rows": [
                    {"label": "Perfil", "value": "GENERICO"},
                    {"label": "Baud", "value": f"{baudrate} 8-N-1"},
                ],
            }
        ]

        return SwitchAuditOutcome(
            vendor_id=self.vendor_id,
            vendor_name=self.display_name,
            category=norm_cat,
            hostname=hostname,
            overall_status="OK",
            summary=f"{hostname}: Captura generica OK",
            details=details,
            detail_pages=detail_pages,
            switch_audit=switch_audit,
            raw_commands=command_outputs,
        )

